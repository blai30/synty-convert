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

import collections
import fnmatch
import json
import math
import os
import re
import struct
import sys
import time
import traceback
import urllib.parse

import bmesh
import bpy
import numpy
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import material_flavors
import split_character_head
import texture_matching

RESULT_PREFIX = "@@RESULT "

# Synty FBX come from Maya: centimeter units and a Y-up axis, which Blender's importer
# parks on the armature object as scale 0.01 plus a 90 degree X rotation.
UNIFORM_SCALE_TOLERANCE = 1e-5
IDENTITY_TOLERANCE = 1e-6
STATIC_CURVE_TOLERANCE = 1e-6

# Blender's FBX importer drives one Principled BSDF socket per FBX material property. These
# are the four Synty's files actually connect anything to, and the name each becomes in a
# material record.
CHANNEL_SOCKETS = {"albedo": "Base Color", "alpha": "Alpha", "emission": "Emission Color",
                   "normal": "Normal"}

# What the importer produces for a material that declares no shading properties at all,
# which is 93% of the corpus: FBX Shininess defaults to 20 and Blender converts it with
# roughness = 1 - sqrt(shininess) / 10. Derived rather than written out so a material that
# says nothing is never mistaken for one that asked for this exact value.
DEFAULT_ROUGHNESS = round(1.0 - math.sqrt(20.0) / 10.0, 4)

# Coverage below this is a hole. FBX says only that a mask is bound, never how to apply it;
# every Synty mask is a cutout, and cutouts sort correctly where blending does not.
ALPHA_CUTOFF = 0.5

# How Synty names a level of a foliage LOD chain: SM_Env_Tree_Meadow_01_LOD0 through _LOD3,
# with a model's trunk and its canopy each carrying their own chain.
LOD_SUFFIX = re.compile(r"^(?P<base>.+)_LOD(?P<level>\d+)$", re.IGNORECASE)

# Largest island, in triangles, still counted as a leaf card rather than woody geometry.
# Synty's foliage cards are single quads; the trunks and branches they hang off run to
# hundreds of triangles, so anything in between separates the two cleanly.
LEAF_MAX_TRIS = 8

# A mesh holding a model's twigs rather than its trunk and canopy, which needs no splitting.
BRANCH_MESH = "Branches"

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


def fcurve_groups(action):
    """Yield each fcurve collection an action holds, legacy or slotted (Blender 4.4+).

    Removing a curve needs the collection that owns it, which iterating them loses.
    """
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        yield legacy
        return
    for layer in action.layers:
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                yield channelbag.fcurves


def iter_fcurves(action):
    """Yield an action's fcurves across both the legacy and slotted (Blender 4.4+) layouts."""
    for group in fcurve_groups(action):
        yield from group


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


def is_static(fcurve):
    """True when every key holds the same value, so the curve expresses no motion."""
    values = [point.co[1] for point in fcurve.keyframe_points]
    return len(values) < 2 or max(values) - min(values) < STATIC_CURVE_TOLERANCE


def drop_static_takes():
    """Remove object-level transform curves that never change value.

    Synty exports a single-key 'Take 001' onto props that are not animated at all, which
    just restates the importer's centimeter and Y-up transform. It carries no motion, so
    all it does is block normalization and then, in the exported clip, reapply the very
    transform normalization exists to remove. Pose-bone channels are left alone; they are
    what real character takes are made of.
    """
    actions = {action for obj in bpy.data.objects for action in actions_of(obj)}
    for action in actions:
        for group in fcurve_groups(action):
            for fcurve in [curve for curve in group
                           if not curve.data_path.startswith("pose.bones[") and is_static(curve)]:
                group.remove(fcurve)
    # An action left with no channels exports as nothing, so drop it outright rather than
    # let the scene claim a take the GLB does not carry.
    for action in actions:
        if not list(iter_fcurves(action)):
            bpy.data.actions.remove(action)


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
            # Bone space just shrank by the same factor the rest pose did, and a bone
            # parented child carries none of that on its own transform: the importer folds
            # it into the parent inverse instead. So the factor is the whole of what has to
            # be pushed down, and it has to reach the child's mesh and not just its offset.
            # Rescaling the offset alone leaves a Synty ballista's bolt a hundred times the
            # size of the ballista.
            if factor is not None:
                bake_hierarchy(child, Matrix.Scale(factor, 4), warnings)
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


def image_behind(socket):
    """The image node feeding a socket, seen through the Normal Map node when there is one."""
    if not socket.is_linked:
        return None
    node = socket.links[0].from_node
    if node.type == "NORMAL_MAP":
        return image_behind(node.inputs["Color"])
    return node if node.type == "TEX_IMAGE" and node.image is not None else None


def texture_reference(node):
    """The file an image node asks for, or None when the FBX named no file at all.

    Some Synty materials carry a texture slot whose path was emptied before export. Blender
    falls back to the FBX object name for those, which is not a file name and cannot be
    resolved against anything, so there is nothing to carry across.
    """
    name = os.path.basename(node.image.filepath.replace("\\", "/")) or node.image.name
    suffix = os.path.splitext(name)[1].lower()
    return name if suffix in texture_matching.TEXTURE_SUFFIXES else None


def socket_value(bsdf, name, fallback):
    """A Principled input's own value, tolerating sockets a Blender version may not have."""
    socket = bsdf.inputs.get(name)
    if socket is None:
        return fallback
    value = socket.default_value
    if isinstance(fallback, list):
        return [round(channel, 4) for channel in value[:3]]
    return round(value, 4)


def describe_material(material):
    """Read every channel Blender's FBX importer populated from the source material.

    Channels are read by following the link into each Principled socket rather than by
    walking the node list: image nodes are created in FBX connection order, so the first
    one in a material is the emissive map as often as it is the albedo.
    """
    info = {"source": material.name, "references": dict.fromkeys(CHANNEL_SOCKETS),
            "color": [1.0, 1.0, 1.0], "alpha": 1.0, "emission_color": [0.0, 0.0, 0.0],
            "emission_strength": 1.0, "roughness": DEFAULT_ROUGHNESS, "metallic": 0.0,
            "normal_strength": 1.0}
    tree = material.node_tree if material.use_nodes else None
    bsdf = tree.nodes.get("Principled BSDF") if tree is not None else None
    if bsdf is None:
        return info

    for channel, socket in CHANNEL_SOCKETS.items():
        node = image_behind(bsdf.inputs[socket]) if socket in bsdf.inputs else None
        info["references"][channel] = texture_reference(node) if node else None
    # A socket's own value is what the FBX declared for that property. It only describes the
    # material where no map covers it, which is how Maya treats a connected file.
    info["color"] = socket_value(bsdf, "Base Color", info["color"])
    info["alpha"] = socket_value(bsdf, "Alpha", info["alpha"])
    info["emission_color"] = socket_value(bsdf, "Emission Color", info["emission_color"])
    info["emission_strength"] = socket_value(bsdf, "Emission Strength", info["emission_strength"])
    info["roughness"] = socket_value(bsdf, "Roughness", info["roughness"])
    info["metallic"] = socket_value(bsdf, "Metallic", info["metallic"])
    normal = bsdf.inputs.get("Normal")
    if normal is not None and normal.is_linked and normal.links[0].from_node.type == "NORMAL_MAP":
        info["normal_strength"] = round(normal.links[0].from_node.inputs["Strength"].default_value, 4)
    return info


def tokens_of(text):
    return [token for token in re.split(r"[^A-Za-z0-9]+", text) if token]


def stem_of(channel):
    """The texture name a channel settled on, resolved where possible and asked-for where not."""
    named = channel.get("texture_source") or channel.get("reference") or ""
    return os.path.splitext(os.path.basename(named))[0]


def unshared_tail(stem, base):
    """The part of a map's name that the material's base name does not already say.

    PolygonSciFiSpace_Emissive_01 against a PolygonSciFiSpace_Texture_01_A material is just
    Emissive_01, which keeps a qualified name readable while still telling the pack's two
    emissive maps apart.
    """
    stem_tokens, base_tokens = tokens_of(stem), tokens_of(base)
    shared = 0
    while (shared < len(stem_tokens) and shared < len(base_tokens)
           and stem_tokens[shared].lower() == base_tokens[shared].lower()):
        shared += 1
    return "_".join(stem_tokens[shared:] or stem_tokens)


def hex_of(color):
    return "".join("%02X" % min(255, max(0, round(channel * 255))) for channel in color)


def base_name(record):
    """What a material is called before anything beyond its atlas is taken into account."""
    albedo = record["channels"].get("albedo") or {}
    if albedo.get("texture_source"):
        return os.path.splitext(os.path.basename(albedo["texture_source"]))[0]
    if albedo.get("reference"):
        return re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(albedo["reference"])[0]).strip("_")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", record["source"]).strip("_")
    # glass, glass1 and glass2 are the same material in three files.
    cleaned = re.sub(r"\d+$", "", cleaned) or "Material"
    return cleaned[:1].upper() + cleaned[1:]


def canonical_name(record):
    """A stable, meaningful name shared by every mesh whose material is identical.

    Materials are named for their atlas, since Synty's own names are Maya leftovers that are
    ambiguous across files: lambert1 alone maps to four textures. Every property that would
    make two materials render differently then adds a qualifier, so a material that also
    carries an emissive map cannot collapse into the plain one wearing the same atlas and
    quietly lose it. Qualifiers come from the material itself and never from the order files
    happen to be converted in, so one name means one thing across a whole pack.
    """
    channels = record["channels"]
    base = base_name(record)
    parts = [base]

    if channels.get("emission"):
        parts.append(unshared_tail(stem_of(channels["emission"]), base))
    elif any(record["emission_color"]):
        parts.append("Emissive" + hex_of(record["emission_color"]))
    if channels.get("normal"):
        parts.append(unshared_tail(stem_of(channels["normal"]), base))
    if channels.get("alpha"):
        parts.append("Cutout")
    elif record["alpha"] < 0.999:
        parts.append("A%02d" % round(record["alpha"] * 100))
    if record["roughness"] != DEFAULT_ROUGHNESS:
        parts.append("R%02d" % round(record["roughness"] * 100))
    if record["metallic"]:
        parts.append("M%02d" % round(record["metallic"] * 100))
    if not channels.get("albedo"):
        # Nothing above names the colour, and colour is all an untextured material is.
        parts.append(hex_of(record["color"]))
    return re.sub(r"_+", "_", "_".join(part for part in parts if part)).strip("_")


def resolve_texture(reference, context, warnings, channel):
    """Resolve one texture reference to the mirrored copy of it in the output tree."""
    if not reference:
        return None
    source_root = context.get("source_root", "")
    output_root = context.get("output_root", "")
    match = texture_matching.resolve(reference, context.get("textures", []),
                                     context.get("overrides", {}), context.get("foreign", {}))
    label = "" if channel == "albedo" else channel + " "
    texture_path = None
    if match:
        # Point at the mirrored copy in the output tree, not the source pack.
        relative = os.path.relpath(match.path, source_root)
        candidate = os.path.join(output_root, relative)
        if os.path.exists(candidate):
            texture_path = candidate
        elif output_root and os.path.isdir(output_root):
            # Absent output pack means this is a scan, not a conversion.
            if relative.startswith(os.pardir):
                # An override reached into a pack that has not been converted, so the
                # mirrored texture it points at does not exist yet.
                warnings.append(f"cross-pack {label}texture needs its pack converted too: "
                                f"{os.path.normpath(relative)}")
            else:
                warnings.append(f"{label}texture not mirrored yet: {relative}")
    else:
        warnings.append(f"unresolved {label}texture reference '{reference}'")
    return {"texture": texture_path, "texture_source": match.path if match else None,
            "reference": reference, "method": match.method if match else None,
            "score": match.score if match else None}


def transparency_of(record):
    """How a material is meant to blend: a bound mask cuts out, a bare value fades.

    A mask that never resolved leaves nothing to cut with, so the material stays opaque
    rather than claiming a cutout an engine would then set up a shader for and never use.
    The name still records that one was asked for, and resolving it later brings it back.
    """
    if (record["channels"].get("alpha") or {}).get("texture_source"):
        return "scissor"
    if record["alpha"] < 0.999:
        return "alpha"
    return None


def flavor_fill(context, source, material_name):
    """The declared default for a material that resolved to no texture of its own.

    Shaped exactly like resolve_texture's return so everything downstream, the canonical
    name above all, cannot tell the difference between a texture the FBX asked for and one
    this supplied.
    """
    declared = context.get("materials") or {}
    binding = material_flavors.match_binding(declared.get("bind") or [],
                                             os.path.splitext(os.path.basename(source))[0],
                                             material_name)
    if not binding:
        return None
    member = (declared.get("sets") or {})[binding["flavor"]]["default"]
    relative = member.replace("/", os.sep)
    candidate = os.path.join(context.get("output_root", ""), relative)
    # Mirrors resolve_texture: an output pack that does not exist yet means this is a scan,
    # and claiming a mirrored path that is not on disk would put a phantom file into the
    # scan report that pack authors read.
    return {"texture": candidate if os.path.exists(candidate) else None,
            "texture_source": os.path.join(context.get("source_root", ""), relative),
            "reference": None, "method": "flavor", "score": None,
            "flavor": binding["flavor"], "binding": binding["material"]}


def resolve_materials(context, source, warnings):
    """Map every imported material to a canonical record. Makes no changes to the scene."""
    context = context or {}
    resolved = {}
    for material in bpy.data.materials:
        info = describe_material(material)
        references = dict(info["references"])
        # A mask is nearly always the very file the material is coloured with, so resolving
        # it a second time would only repeat the work and any warning that came with it.
        mask_is_albedo = references["alpha"] and references["alpha"] == references["albedo"]
        if mask_is_albedo:
            references["alpha"] = None
        channels = {channel: resolve_texture(reference, context, warnings, channel)
                    for channel, reference in references.items()}
        if mask_is_albedo:
            channels["alpha"] = channels["albedo"]
        record = {key: info[key] for key in
                  ("source", "color", "alpha", "emission_color", "emission_strength",
                   "roughness", "metallic", "normal_strength")}
        record["channels"] = {name: found for name, found in channels.items() if found}
        # glTF hangs coverage on the base colour texture, so a material that binds only a
        # mask has nowhere to put it. The mask becomes its colour as well, which is what the
        # eleven Military chain-link fences built this way were always going to look like.
        if record["channels"].get("alpha") and not record["channels"].get("albedo"):
            record["channels"]["albedo"] = record["channels"]["alpha"]
        # Last, so a texture the FBX actually named always wins over a declared default,
        # and so a mask promoted to colour above is not overwritten by one.
        if not (record["channels"].get("albedo") or {}).get("texture_source"):
            filled = flavor_fill(context, source, info["source"])
            if filled:
                record["channels"]["albedo"] = filled
        record["transparency"] = transparency_of(record)
        record["name"] = canonical_name(record)
        resolved[material.name] = record
    return resolved


def texture_image(path, colorspace, cache):
    """Load a texture once per colour space it is needed in.

    A mask bound as both colour and coverage has to exist twice, since a normal or alpha map
    is data and the same file read as colour is not.
    """
    key = (path, colorspace)
    if key not in cache:
        image = bpy.data.images.load(path, check_existing=True)
        if image.colorspace_settings.name != colorspace:
            image = image.copy() if image.users else image
            image.colorspace_settings.name = colorspace
        cache[key] = image
    return cache[key]


def build_material(name, record, cache, warnings):
    """Rebuild a material with every channel the FBX declared, on the shipped textures."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    channels = record["channels"]
    albedo = (channels.get("albedo") or {}).get("texture")
    mask = (channels.get("alpha") or {}).get("texture")
    emission = (channels.get("emission") or {}).get("texture")
    normal = (channels.get("normal") or {}).get("texture")

    # Maya ignores a material's own colour once a file is connected over it, so a value only
    # applies where no map does.
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0) if albedo else (*record["color"], 1.0)
    bsdf.inputs["Alpha"].default_value = record["alpha"]
    bsdf.inputs["Roughness"].default_value = record["roughness"]
    bsdf.inputs["Metallic"].default_value = record["metallic"]
    if "Emission Color" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = ((1.0, 1.0, 1.0, 1.0) if emission
                                                       else (*record["emission_color"], 1.0))
        bsdf.inputs["Emission Strength"].default_value = record["emission_strength"]

    base_node = None
    if albedo:
        base_node = tree.nodes.new("ShaderNodeTexImage")
        base_node.image = texture_image(albedo, "sRGB", cache)
        tree.links.new(bsdf.inputs["Base Color"], base_node.outputs["Color"])
    # glTF carries coverage on the base colour texture's alpha channel rather than in a map
    # of its own, so a mask can only be applied where it is that same texture.
    if mask and base_node is not None:
        if mask != albedo:
            warnings.append(f"'{name}' masks with a different file than it colours with, "
                            f"which glTF cannot express; left opaque")
        elif base_node.image.channels < 4 or base_node.image.depth in {24, 8}:
            warnings.append(f"'{name}' binds '{os.path.basename(mask)}' as a mask but the file "
                            f"has no alpha channel; left opaque")
        else:
            # glTF has no cutout flag. The exporter reads alphaMode MASK off a threshold in
            # front of the Alpha socket, which is what an engine then loads as alpha scissor.
            clip = tree.nodes.new("ShaderNodeMath")
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = ALPHA_CUTOFF
            tree.links.new(clip.inputs[0], base_node.outputs["Alpha"])
            tree.links.new(bsdf.inputs["Alpha"], clip.outputs["Value"])
    if emission:
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = texture_image(emission, "sRGB", cache)
        tree.links.new(bsdf.inputs["Emission Color"], node.outputs["Color"])
    if normal:
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = texture_image(normal, "Non-Color", cache)
        mapping = tree.nodes.new("ShaderNodeNormalMap")
        mapping.inputs["Strength"].default_value = record["normal_strength"]
        tree.links.new(mapping.inputs["Color"], node.outputs["Color"])
        tree.links.new(bsdf.inputs["Normal"], mapping.outputs["Normal"])
    if record["transparency"]:
        # Only reaches Blender's own viewport; the exporter reads alpha off the nodes.
        try:
            material.blend_method = "BLEND"
        except (AttributeError, TypeError):
            pass
    return material


def distinct_materials(resolved):
    records = {}
    for entry in resolved.values():
        records.setdefault(entry["name"], entry)
    return list(records.values())


def material_indices(mesh):
    """The material slot each polygon is assigned to."""
    buffer = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get("material_index", buffer)
    return buffer


def rebuild_materials(context, source, warnings):
    """Replace every imported material with a canonically named, deduplicated one.

    Returns one record per distinct material for the CLI to turn into a Godot manifest.
    """
    resolved = resolve_materials(context, source, warnings)
    # Capture slot assignments, then rebuild from scratch so names cannot collide. Emptying
    # a mesh's slots also resets every polygon's material_index to zero, so the per-face
    # assignment has to be carried across by hand; without it a multi-material mesh
    # collapses onto whatever sits in its first slot, which is how a castle wall ends up
    # wearing the pack's atlas stretched over it instead of its own tiling brick texture.
    plan = [(mesh,
             [resolved.get(m.name, {}).get("name") if m else None for m in mesh.materials],
             material_indices(mesh))
            for mesh in bpy.data.meshes]
    records = {entry["name"]: entry for entry in distinct_materials(resolved)}

    strip_materials()
    cache = {}
    created = {name: build_material(name, entry, cache, warnings)
               for name, entry in records.items()}
    for mesh, names, indices in plan:
        mesh.materials.clear()
        for name in names:
            mesh.materials.append(created.get(name) if name else None)
        mesh.polygons.foreach_set("material_index", indices)
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
    """Delete cameras, lights and Unreal collision hulls; Godot supplies its own.

    A ``UCX_`` prefix is Unreal's convention for a collision mesh. Godot reads no meaning
    into the name, so a hull left in the export arrives as a visible untextured box
    sitting over the prop it was meant to bound.
    """
    for obj in list(bpy.data.objects):
        if obj.type in {"CAMERA", "LIGHT", "SPEAKER"} or obj.name.upper().startswith("UCX_"):
            bpy.data.objects.remove(obj, do_unlink=True)


def lod_chains():
    """Mesh objects grouped by the name they share ahead of their ``_LOD`` suffix."""
    chains = collections.defaultdict(list)
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        # Blender disambiguates a name collision with a .001 suffix, which would otherwise
        # hide the level behind it and leave that whole chain in the export.
        match = LOD_SUFFIX.match(re.sub(r"\.\d+$", "", obj.name))
        if match:
            chains[match.group("base")].append((int(match.group("level")), obj))
    return chains


def drop_extra_lods():
    """Keep the finest level of every LOD chain and delete the rest.

    Nothing downstream reads these as a chain. Godot has no LOD group, takes no meaning from
    the naming and does not implement ``MSFT_lod``, so all four levels arrive as ordinary
    sibling meshes and render at once, the coarsest being a billboard imposter crossing the
    very tree it exists to stand in for at distance. Godot generates its own chain from
    whatever it imports, which makes the finest level the only one worth shipping.
    """
    dropped = 0
    for members in lod_chains().values():
        # Finest first. That is whichever level is lowest rather than zero specifically,
        # since a pack that starts counting elsewhere still has a finest level.
        for _, obj in sorted(members, key=lambda member: member[0])[1:]:
            # Removing a parent would strand its children on a stale parent inverse.
            if obj.children:
                continue
            bpy.data.objects.remove(obj, do_unlink=True)
            dropped += 1
    return dropped


def island_triangles(mesh):
    """Triangle count of the connected island each polygon belongs to.

    Coincident vertices are welded first. Nothing here has been through glTF yet, but Maya
    leaves a trunk built from separately modelled sections meeting at unmerged vertices,
    which would otherwise read as dozens of islands instead of one.
    """
    working = bmesh.new()
    working.from_mesh(mesh)
    working.faces.ensure_lookup_table()
    # Welding can drop a face that collapses, which would renumber everything after it.
    # Carrying each face's own index through the op keeps the answer keyed to the mesh
    # this was asked about rather than to whatever bmesh is left holding.
    origin = working.faces.layers.int.new("origin")
    for face in working.faces:
        face[origin] = face.index
    bmesh.ops.remove_doubles(working, verts=working.verts, dist=1e-4)
    working.faces.ensure_lookup_table()

    sizes = {}
    seen = set()
    for face in working.faces:
        if face.index in seen:
            continue
        stack, island = [face], []
        seen.add(face.index)
        while stack:
            current = stack.pop()
            island.append(current)
            for edge in current.edges:
                for neighbour in edge.link_faces:
                    if neighbour.index not in seen:
                        seen.add(neighbour.index)
                        stack.append(neighbour)
        total = sum(len(polygon.verts) - 2 for polygon in island)
        for polygon in island:
            sizes[polygon[origin]] = total
    working.free()
    return sizes


def foliage_parts(context, source):
    """The parts a pack names for this model, or None when it names none."""
    stem = os.path.splitext(os.path.basename(source))[0]
    for pattern, parts in (context.get("foliage") or {}).items():
        if fnmatch.fnmatch(stem, pattern):
            return parts
    return None


def foliage_texture(suffix, context, warnings):
    """The shipped texture a foliage override names, matched as a path suffix."""
    wanted = suffix.replace("\\", "/")
    for path in context.get("textures", []):
        if path.replace("\\", "/").endswith(wanted):
            return path
    warnings.append(f"foliage texture not found in pack: {suffix}")
    return None


def foliage_material(name, path, cutout):
    """Stand in for the texture binding the FBX was exported without.

    Built as an imported material would have arrived rather than as a finished one, so that
    everything downstream, resolution and naming and the manifest, treats it as a material
    that named a file all along.
    """
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    # Blender's own defaults for a new node are not the importer's: it starts a material at
    # roughness 0.5 and emission white, which would qualify every one of these names with an
    # R50 and an EmissiveFFFFFF and light them up in the generated .tres. What the FBX would
    # have declared is a plain material, so say that.
    bsdf.inputs["Roughness"].default_value = DEFAULT_ROUGHNESS
    bsdf.inputs["Metallic"].default_value = 0.0
    if "Emission Color" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(path, check_existing=True)
    material.node_tree.links.new(bsdf.inputs["Base Color"], texture.outputs["Color"])
    if cutout:
        # Coverage rides on the same file, which is what a Synty foliage card always does
        # and what lets the material come out as a cutout rather than an opaque quad.
        material.node_tree.links.new(bsdf.inputs["Alpha"], texture.outputs["Alpha"])
    return material


def apply_foliage_textures(context, source, warnings):
    """Bind the textures a foliage model's FBX declares nothing for, splitting where needed.

    Synty's Nature Biomes foliage exports with its material bindings stripped: one grey
    Lambert covers trunk and canopy alike, and since every leaf card maps the whole of UV
    space, the trunk's own UVs sit underneath them and no single image can serve both. The
    parts are separable by geometry though. A leaf card is one quad; a trunk is a single
    island of hundreds of triangles, so splitting on island size recovers the two materials
    the model was authored with. See ``foliage_overrides.json`` for what each model gets.
    """
    parts = foliage_parts(context, source)
    if not parts:
        return 0
    resolved = {part: foliage_texture(suffix, context, warnings)
                for part, suffix in parts.items()}
    touched = 0
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        if BRANCH_MESH in obj.name:
            if resolved.get("branches"):
                obj.data.materials.clear()
                obj.data.materials.append(foliage_material("Branches", resolved["branches"], True))
                touched += 1
            continue
        if not resolved.get("canopy"):
            continue
        sizes = island_triangles(obj.data)
        woody = [polygon.index for polygon in obj.data.polygons
                 if sizes.get(polygon.index, 0) > LEAF_MAX_TRIS]
        obj.data.materials.clear()
        # Canopy first, so a model with no woody geometry needs no second slot at all.
        obj.data.materials.append(foliage_material("Canopy", resolved["canopy"], True))
        if woody and resolved.get("trunk"):
            obj.data.materials.append(foliage_material("Trunk", resolved["trunk"], False))
            for index in woody:
                obj.data.polygons[index].material_index = 1
        touched += 1
    # The Lambert that carried no texture is what these replaced, so leaving it behind would
    # put a material no mesh wears into the manifest, still reported as unresolved.
    for material in list(bpy.data.materials):
        if material.users == 0:
            bpy.data.materials.remove(material)
    return touched


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


def binds_a_texture():
    """True when some mesh in the scene wears a material that bound an image.

    What ``--untextured drop`` and ``--untextured fill-or-drop`` ask. Read off the
    rebuilt materials the meshes actually wear rather than off the resolution records,
    so a material that resolved but that no mesh ended up wearing cannot vouch for a
    model that still ships white. Any channel counts: a model carrying only an
    emission or normal map has real texture data on it.
    """
    for obj in real_meshes():
        for material in obj.data.materials:
            if not material or not material.use_nodes:
                continue
            if any(node.type == "TEX_IMAGE" and node.image for node in material.node_tree.nodes):
                return True
    return False


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
    # Multiplies the unit conversion the FBX asks for, which is 1.0 unless the pack
    # declares a unit its geometry is not actually in. See scale_overrides.json.
    import_options["global_scale"] = job.get("scale", 1.0)
    bpy.ops.import_scene.fbx(filepath=src, **import_options)

    mode = options.get("untextured", "fill")
    context = packs.get(job.get("pack"), {})
    if mode in ("keep", "drop"):
        # Filling is off, so the worker must not see a binding table it would act on.
        # --untextured keep --scan-materials is therefore how you see what is still bare.
        context = {key: value for key, value in context.items() if key != "materials"}

    if options.get("scan_only"):
        # Report what the materials resolve to without writing output. Foliage bindings are
        # applied first even though nothing is written, or the scan would report a tree as
        # untextured that a conversion of the same pack textures perfectly well.
        apply_foliage_textures(context, src, warnings)
        resolved = resolve_materials(context, src, warnings)
        return {"src": src, "dst": dst, "pack": job.get("pack"), "src_bytes": os.path.getsize(src),
                "dst_bytes": 0, "materials": distinct_materials(resolved), "warnings": warnings,
                "summary": scene_summary(), "normalized_scale": None}

    if options.get("vertex_colors") != "keep":
        drop_vertex_colors()
    drop_non_geometry()
    # Ahead of the bounds invariant below, so what it compares is the geometry that ships.
    dropped_lods = drop_extra_lods() if options.get("lods", "drop") != "keep" else 0
    # Ahead of everything downstream, so the two halves are normalized, materialized and
    # exported as ordinary meshes rather than needing a second pass over the finished GLB.
    split = split_character_head.split_scene(warnings) if job.get("split") else []
    # Ahead of material resolution, which is what turns these bindings into real materials.
    foliage = apply_foliage_textures(context, src, warnings) if external else 0
    materials = rebuild_materials(context, src, warnings) if external else []
    if not external:
        strip_materials()

    # Ahead of the remaining work, all of which would be spent on a file about to be thrown
    # away. Geometry is the qualifier: an animation file carries a skeleton and no mesh, so
    # it has nothing to texture and is not what this is meant to catch.
    if mode in ("drop", "fill-or-drop") and real_meshes() and not binds_a_texture():
        # A previous run without the flag would have left one here, and leaving it would
        # put back exactly the model this was asked to keep out.
        if os.path.exists(dst):
            os.remove(dst)
        return {"src": src, "dst": dst, "pack": job.get("pack"), "untextured": True,
                "src_bytes": os.path.getsize(src), "dst_bytes": 0, "materials": [],
                "warnings": warnings}

    # Neither dropping takes nor normalizing may move anything, so the world bounds are an
    # always-on invariant across both.
    bounds_before = scene_bounds()
    drop_static_takes()
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
        # A normal map is meaningless without a tangent basis. glTF lets a loader generate
        # one, but shipping it keeps the handful of models that carry normals right in any
        # engine, and costs nothing on the thousands that do not.
        if any("normal" in record["channels"] for record in materials):
            export_options["export_tangents"] = True
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
        "dropped_lods": dropped_lods,
        "foliage": foliage,
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
