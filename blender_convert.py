"""FBX to GLB conversion, executed inside Blender.

Invoked as::

    blender --background --factory-startup --python blender_convert.py -- <jobfile.json>

The job file holds ``{"options": {...}, "jobs": [{"src": ..., "dst": ...}, ...]}``.
One ``@@RESULT {json}`` line is printed per job for the parent process to collect.

Each job runs the same pipeline: import the FBX, strip every material, normalize the
armature so Godot receives an identity-transform Skeleton3D, then export a GLB. Jobs
flagged ``split`` also have any character head separated onto its own node first; see
``split_character_head.py``.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import time
import traceback
import urllib.parse

import bpy
import numpy
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_character_head
import texture_matching

RESULT_PREFIX = "@@RESULT "

# Synty FBX come from Maya: centimeter units and a Y-up axis, which Blender's importer
# parks on the armature object as scale 0.01 plus a 90 degree X rotation.
UNIFORM_SCALE_TOLERANCE = 1e-5
IDENTITY_TOLERANCE = 1e-6

FBX_IMPORT_OPTIONS = {
    "use_anim": True,
    # Never let the importer crawl the pack looking for texture files.
    "use_image_search": False,
    "use_custom_props": False,
    # Preserve the source skeleton exactly so clips stay compatible across packs.
    "ignore_leaf_bones": True,
    "automatic_bone_orientation": False,
    "use_prepost_rot": True,
    "use_subsurf": False,
}

GLTF_EXPORT_OPTIONS = {
    "export_format": "GLB",
    # glTF and Godot are both Y-up, right-handed.
    "export_yup": True,
    "export_apply": True,
    # Barebones meshes: no materials, no images, no textures.
    "export_materials": "NONE",
    "export_image_format": "NONE",
    "export_unused_images": False,
    "export_unused_textures": False,
    # UVs stay so the pack's texture atlas can be reapplied in Godot.
    "export_texcoords": True,
    "export_normals": True,
    # Godot regenerates tangents on import, so shipping them is wasted bytes.
    "export_tangents": False,
    "export_attributes": False,
    "export_skins": True,
    "export_influence_nb": 4,
    "export_all_influences": False,
    "export_morph": True,
    "export_cameras": False,
    "export_lights": False,
    "export_extras": False,
    "export_leaf_bone": False,
    # Keep every bone; Synty rigs carry attachment bones that carry no weights.
    "export_def_bones": False,
    "export_rest_position_armature": True,
    "export_reset_pose_bones": True,
    "export_animations": True,
    "export_animation_mode": "ACTIONS",
    "export_bake_animation": False,
    "export_force_sampling": False,
    "export_optimize_animation_size": True,
    "export_optimize_animation_keep_anim_armature": True,
    # Clips that start at frame 2 would otherwise open with a dead zone.
    "export_anim_slide_to_zero": True,
    "export_negative_frame": "CROP",
    "export_current_frame": False,
    "export_nla_strips": False,
    "export_shared_accessors": True,
    # Godot's glTF importer rejects files that require Draco.
    "export_draco_mesh_compression_enable": False,
}


def supported_options(operator, options):
    """Drop keys the running Blender build does not expose, so the tool survives upgrades."""
    known = {p.identifier for p in operator.get_rna_type().properties}
    return {key: value for key, value in options.items() if key in known}


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def activate(obj):
    """Make ``obj`` the sole selected and active object."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def iter_fcurves(action):
    """Yield an action's fcurves across both the legacy and slotted (Blender 4.4+) layouts."""
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        yield from legacy
        return
    for layer in action.layers:
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                yield from channelbag.fcurves


def actions_of(obj):
    """Every action reachable from an object, via its active action or its NLA strips."""
    anim = obj.animation_data
    if anim is None:
        return []
    found = []
    if anim.action is not None:
        found.append(anim.action)
    for track in anim.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                found.append(strip.action)
    return found


def scale_bone_translations(action, factor):
    """Rescale pose-bone location channels after bone rest lengths changed."""
    for fcurve in iter_fcurves(action):
        if not fcurve.data_path.startswith("pose.bones[") or not fcurve.data_path.endswith(".location"):
            continue
        points = fcurve.keyframe_points
        count = len(points)
        if not count:
            continue
        for attribute in ("co", "handle_left", "handle_right"):
            buffer = [0.0] * (count * 2)
            points.foreach_get(attribute, buffer)
            # Every odd slot is the value; the even slot is the frame number.
            buffer[1::2] = [value * factor for value in buffer[1::2]]
            points.foreach_set(attribute, buffer)
        fcurve.update()


BAKEABLE_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META", "LATTICE", "ARMATURE"}


def has_object_level_animation(obj):
    """True when an object animates its own transform, which baking would invalidate."""
    for action in actions_of(obj):
        for fcurve in iter_fcurves(action):
            if not fcurve.data_path.startswith("pose.bones["):
                return True
    return False


def normalize_transforms(warnings):
    """Bake the FBX unit and axis conversion out of every object transform.

    Blender's importer parks Maya's centimeter units and Y-up axis on the top-level
    objects as scale 0.01 plus a 90 degree X rotation. Exported as-is that becomes a
    Node3D or Skeleton3D in Godot that is rotated and scaled to 1/100, which throws off
    every BoneAttachment3D, collision shape and root-motion value. Folding it into the
    mesh vertices, bone rest pose and animation curves leaves identity nodes instead.
    """
    roots = [obj for obj in bpy.data.objects if obj.parent is None]
    if any(has_object_level_animation(obj) for obj in bpy.data.objects):
        warnings.append("object-level animation present; transforms left as imported")
        return None

    baked = None
    for root in roots:
        result = bake_hierarchy(root, Matrix.Identity(4), warnings)
        baked = result if result is not None else baked
    return baked


def bake_hierarchy(obj, inherited, warnings):
    """Strip rotation and scale from an object's local transform, folding them into its data.

    ``inherited`` is the rotation and scale already removed from the ancestors, which has
    to be pushed back down so world placement is preserved. Recurses depth first and
    returns the uniform scale factor that was baked, if any.
    """
    local = inherited @ obj.matrix_parent_inverse @ obj.matrix_basis
    translation, rotation, scale = local.decompose()
    rotation_scale = Matrix.LocRotScale(None, rotation, scale)

    # Captured before any mutation reparents or rewrites these matrices.
    children = list(obj.children)
    child_locals = {child: child.matrix_parent_inverse @ child.matrix_basis for child in children}

    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_basis = local

    is_identity = (abs(rotation.angle) < IDENTITY_TOLERANCE
                   and max(abs(axis - 1.0) for axis in scale) < IDENTITY_TOLERANCE)
    factor = None
    if not is_identity:
        if obj.type in BAKEABLE_TYPES and obj.data is not None:
            if obj.data.users > 1:
                warnings.append(f"'{obj.name}' shares mesh data; transform left on the node")
            else:
                activate(obj)
                try:
                    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
                    obj.matrix_basis = Matrix.Translation(translation)
                except RuntimeError as error:
                    warnings.append(f"'{obj.name}' could not be baked: {error}")
        else:
            # Empties carry no data, so the transform simply moves onto the children.
            obj.matrix_basis = Matrix.Translation(translation)

        if max(scale) - min(scale) <= UNIFORM_SCALE_TOLERANCE and min(scale) > 0.0:
            factor = scale.x
        else:
            warnings.append(f"'{obj.name}' has non-uniform scale {tuple(round(v, 5) for v in scale)}")

    if obj.type == "ARMATURE" and factor is not None:
        # Bone-local translation curves are expressed in the rest pose's units.
        for action in actions_of(obj):
            scale_bone_translations(action, factor)

    for child in children:
        child.matrix_parent_inverse = Matrix.Identity(4)
        child.matrix_basis = child_locals[child]
    for child in children:
        if child.parent_type == "BONE":
            # Bone space just shrank by the same factor the rest pose did.
            if factor is not None:
                child.matrix_basis.translation = child.matrix_basis.translation * factor
        else:
            result = bake_hierarchy(child, rotation_scale, warnings)
            factor = factor if factor is not None else result

    return factor


def strip_materials():
    """Remove every material, image and texture so the meshes ship barebones."""
    for obj in bpy.data.objects:
        data = obj.data
        if data is not None and hasattr(data, "materials"):
            data.materials.clear()
        for slot in obj.material_slots:
            slot.material = None
    for collection in (bpy.data.materials, bpy.data.images, bpy.data.textures):
        for datablock in list(collection):
            collection.remove(datablock)


def describe_material(material):
    """Read the texture reference, colour and alpha out of an imported FBX material."""
    info = {"source": material.name, "reference": None, "color": [1.0, 1.0, 1.0], "alpha": 1.0}
    if not material.use_nodes or material.node_tree is None:
        return info
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None and info["reference"] is None:
            path = node.image.filepath.replace("\\", "/")
            info["reference"] = os.path.basename(path) or node.image.name
        elif node.type == "BSDF_PRINCIPLED":
            info["color"] = [round(value, 4) for value in node.inputs["Base Color"].default_value[:3]]
            info["alpha"] = round(node.inputs["Alpha"].default_value, 4)
    return info


def canonical_name(source_name, texture_path, reference=None):
    """A stable, meaningful material name shared by every mesh that uses it.

    Textured materials are named for their atlas, since Synty's own names are Maya
    leftovers that are ambiguous across files: lambert1 alone maps to four textures.
    An unresolved reference still names the material, so that two different unresolved
    textures cannot collapse into one just because their Maya names normalise alike.
    """
    if texture_path:
        return os.path.splitext(os.path.basename(texture_path))[0]
    if reference:
        return re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(reference)[0]).strip("_")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", source_name).strip("_")
    # glass, glass1 and glass2 are the same material in three files.
    cleaned = re.sub(r"\d+$", "", cleaned) or "Material"
    return cleaned[:1].upper() + cleaned[1:]


def build_material(name, texture_path, color, alpha, transparency):
    """Create a clean Principled BSDF material, optionally driven by an external texture."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Alpha"].default_value = alpha
    if texture_path:
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(texture_path, check_existing=True)
        material.node_tree.links.new(bsdf.inputs["Base Color"], node.outputs["Color"])
        if transparency == "scissor":
            material.node_tree.links.new(bsdf.inputs["Alpha"], node.outputs["Alpha"])
    if transparency:
        try:
            material.blend_method = "BLEND"
        except (AttributeError, TypeError):
            pass
    return material


def resolve_materials(context, warnings):
    """Map every imported material to a canonical record. Makes no changes to the scene."""
    textures = context.get("textures", []) if context else []
    overrides = context.get("overrides", {}) if context else {}
    source_root = context.get("source_root", "") if context else ""
    output_root = context.get("output_root", "") if context else ""

    resolved = {}
    for material in bpy.data.materials:
        info = describe_material(material)
        match = texture_matching.resolve(info["reference"], textures, overrides) if info["reference"] else None
        source_texture = match.path if match else None
        texture_path = None
        if match:
            # Point at the mirrored copy in the output tree, not the source pack.
            relative = os.path.relpath(match.path, source_root)
            candidate = os.path.join(output_root, relative)
            if os.path.exists(candidate):
                texture_path = candidate
            elif output_root and os.path.isdir(output_root):
                # Absent output pack means this is a scan, not a conversion.
                warnings.append(f"texture not mirrored yet: {relative}")
        elif info["reference"]:
            warnings.append(f"unresolved texture reference '{info['reference']}'")
        stem = os.path.splitext(os.path.basename(source_texture))[0] if source_texture else ""
        transparency = None
        if source_texture and "alpha" in stem.lower():
            transparency = "scissor"
        elif info["alpha"] < 0.999:
            transparency = "alpha"
        resolved[material.name] = {
            "name": canonical_name(info["source"], source_texture, info["reference"]),
            "texture": texture_path,
            "texture_source": source_texture,
            "color": info["color"],
            "alpha": info["alpha"],
            "transparency": transparency,
            "source": info["source"],
            "reference": info["reference"],
            "method": match.method if match else None,
            "score": match.score if match else None,
        }
    return resolved


def distinct_materials(resolved):
    records = {}
    for entry in resolved.values():
        records.setdefault(entry["name"], entry)
    return list(records.values())


def rebuild_materials(context, warnings):
    """Replace every imported material with a canonically named, deduplicated one.

    Returns one record per distinct material for the CLI to turn into a Godot manifest.
    """
    resolved = resolve_materials(context, warnings)
    # Capture slot assignments, then rebuild from scratch so names cannot collide.
    slots = {mesh.name: [resolved.get(m.name, {}).get("name") if m else None for m in mesh.materials]
             for mesh in bpy.data.meshes}
    records = {entry["name"]: entry for entry in distinct_materials(resolved)}

    strip_materials()
    created = {name: build_material(name, entry["texture"], entry["color"], entry["alpha"],
                                    entry["transparency"])
               for name, entry in records.items()}
    for mesh in bpy.data.meshes:
        mesh.materials.clear()
        for name in slots.get(mesh.name, []):
            mesh.materials.append(created.get(name) if name else None)
    return list(records.values())


def read_glb(path):
    with open(path, "rb") as handle:
        handle.read(12)
        json_length, _ = struct.unpack("<II", handle.read(8))
        gltf = json.loads(handle.read(json_length))
        binary_length, _ = struct.unpack("<II", handle.read(8))
        blob = handle.read(binary_length)
    return gltf, blob


def write_glb(path, gltf, blob):
    text = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)))
        handle.write(struct.pack("<II", len(text), 0x4E4F534A))
        handle.write(text)
        handle.write(struct.pack("<II", len(blob), 0x004E4942))
        handle.write(blob)


def externalize_images(path, warnings):
    """Make every image a plain external reference relative to the GLB.

    Blender's keep_originals writes a uri *and* a bufferView holding a 250 byte
    placeholder PNG. glTF defines the two as mutually exclusive, so the bufferView is
    dropped; otherwise a loader that prefers it gets the stub instead of the atlas.
    """
    gltf, blob = read_glb(path)
    images = gltf.get("images", [])
    if not images:
        return 0
    base = os.path.dirname(path)
    for image in images:
        uri = image.pop("uri", None)
        image.pop("bufferView", None)
        if uri is None:
            warnings.append(f"image '{image.get('name')}' had no external uri")
            continue
        target = os.path.normpath(os.path.join(base, urllib.parse.unquote(uri)))
        if not os.path.exists(target):
            warnings.append(f"texture missing on disk: {target}")
        relative = os.path.relpath(target, base).replace(os.sep, "/")
        image["uri"] = urllib.parse.quote(relative)
    write_glb(path, gltf, blob)
    return len(images)


def drop_non_geometry():
    """Delete cameras and lights; Godot scenes supply their own."""
    for obj in list(bpy.data.objects):
        if obj.type in {"CAMERA", "LIGHT", "SPEAKER"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def drop_vertex_colors():
    for mesh in bpy.data.meshes:
        for attribute in list(mesh.color_attributes):
            mesh.color_attributes.remove(attribute)


def real_meshes():
    """Mesh objects excluding the icosphere Blender's glTF importer adds as a bone widget."""
    widgets = {bone.custom_shape for obj in bpy.data.objects if obj.type == "ARMATURE"
               for bone in obj.pose.bones if bone.custom_shape}
    return [obj for obj in bpy.data.objects
            if obj.type == "MESH" and obj not in widgets and obj.data.vertices]


def world_coordinates(obj):
    """Every vertex of ``obj`` in world space.

    Computed from vertex data rather than ``Object.bound_box``, which is a cache that
    transform_apply does not refresh. Taking the corners of a local bounding box would
    also be wrong here: the box of a rotated mesh is not the rotation of its box, so
    baking a non-axis-aligned rotation would look like geometry had moved.
    """
    mesh = obj.data
    flat = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float64)
    mesh.vertices.foreach_get("co", flat)
    matrix = numpy.array(obj.matrix_world)
    return flat.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]


def scene_bounds():
    """World-space bounding box of all mesh geometry, or None when there is no mesh."""
    # Rebuilding transforms leaves matrix_world stale until the view layer catches up.
    bpy.context.view_layer.update()
    meshes = real_meshes()
    if not meshes:
        return None
    coordinates = numpy.concatenate([world_coordinates(obj) for obj in meshes])
    return [round(float(value), 4) for value in (*coordinates.min(axis=0), *coordinates.max(axis=0))]


def scene_summary():
    meshes = real_meshes()
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    return {
        "meshes": len(meshes),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "armatures": len(armatures),
        "bones": sum(len(obj.data.bones) for obj in armatures),
        "actions": len(bpy.data.actions),
        "bounds": scene_bounds(),
    }


def bounds_drift(before, after):
    """Largest coordinate difference between two bounding boxes."""
    if before is None or after is None:
        return 0.0 if before == after else float("inf")
    return max(abs(a - b) for a, b in zip(before, after))


def convert(job, options, packs):
    src = job["src"]
    dst = job["dst"]
    warnings = []
    external = options.get("materials", "external") == "external"

    reset_scene()
    import_options = supported_options(bpy.ops.import_scene.fbx, FBX_IMPORT_OPTIONS)
    import_options["colors_type"] = "SRGB" if options.get("vertex_colors") == "keep" else "NONE"
    bpy.ops.import_scene.fbx(filepath=src, **import_options)

    if options.get("scan_only"):
        # Report what the materials resolve to without touching the scene or writing output.
        resolved = resolve_materials(packs.get(job.get("pack"), {}), warnings)
        return {"src": src, "dst": dst, "pack": job.get("pack"), "src_bytes": os.path.getsize(src),
                "dst_bytes": 0, "materials": distinct_materials(resolved), "warnings": warnings,
                "summary": scene_summary(), "normalized_scale": None}

    if options.get("vertex_colors") != "keep":
        drop_vertex_colors()
    drop_non_geometry()
    # Ahead of everything downstream, so the two halves are normalized, materialized and
    # exported as ordinary meshes rather than needing a second pass over the finished GLB.
    split = split_character_head.split_scene(warnings) if job.get("split") else []
    materials = rebuild_materials(packs.get(job.get("pack"), {}), warnings) if external else []
    if not external:
        strip_materials()

    # Normalizing must not move anything, so the world bounds are an always-on invariant.
    bounds_before = scene_bounds()
    applied_scale = normalize_transforms(warnings)
    drift = bounds_drift(bounds_before, scene_bounds())
    if drift > 1e-3:
        warnings.append(f"normalization moved geometry by {drift:.4f} units")

    source_summary = scene_summary()

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    export_options = supported_options(bpy.ops.export_scene.gltf, GLTF_EXPORT_OPTIONS)
    export_options["export_animations"] = options.get("animations", "keep") == "keep"
    if external:
        # Reference the shared texture files instead of embedding a copy per model.
        export_options["export_materials"] = "EXPORT"
        export_options["export_image_format"] = "AUTO"
        export_options["export_keep_originals"] = True
    bpy.ops.export_scene.gltf(filepath=dst, **export_options)
    if external:
        externalize_images(dst, warnings)

    result = {
        "src": src,
        "dst": dst,
        "pack": job.get("pack"),
        "src_bytes": os.path.getsize(src),
        "dst_bytes": os.path.getsize(dst),
        "normalized_scale": applied_scale,
        "summary": source_summary,
        "materials": materials,
        "split": split,
        "warnings": warnings,
    }

    if options.get("verify"):
        result["verify"] = verify_roundtrip(dst, source_summary)
    return result


def verify_roundtrip(path, expected):
    """Reimport the GLB and compare against the source scene.

    Blender's glTF importer applies the Y-up to Z-up conversion, so matching bounds prove
    both the scale and the axis orientation survived the round trip.
    """
    reset_scene()
    bpy.ops.import_scene.gltf(filepath=path)
    actual = scene_summary()
    drift = bounds_drift(expected["bounds"], actual["bounds"])
    checks = {
        # Vertex counts legitimately rise: glTF splits vertices at normal and UV seams.
        "bounds": drift < 1e-3,
        "bones": actual["bones"] == expected["bones"],
        "actions": actual["actions"] == expected["actions"],
        "meshes": actual["meshes"] == expected["meshes"],
        "drift": round(drift, 6),
        "expected": expected,
        "actual": actual,
    }
    checks["ok"] = all(value for value in checks.values() if isinstance(value, bool))
    return checks


def main():
    separator = sys.argv.index("--")
    payload = json.load(open(sys.argv[separator + 1], encoding="utf-8"))
    options = payload["options"]
    packs = payload.get("packs", {})

    for job in payload["jobs"]:
        started = time.monotonic()
        try:
            result = convert(job, options, packs)
            result["ok"] = True
        except Exception:
            result = {"src": job["src"], "dst": job["dst"], "ok": False, "error": traceback.format_exc(limit=3)}
        result["seconds"] = round(time.monotonic() - started, 3)
        print(RESULT_PREFIX + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
