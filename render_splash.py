"""Render the README splash: a floating island of converted models, on transparent alpha.

Run through Blender, not python:

    blender -b -noaudio --python render_splash.py -- --assets assets --out docs/images/splash.png

Cast entries place a model by screen-space coordinates. `u` runs left to right across the
banner and `v` runs from the front edge into the distance, so the table below reads in the
same order it appears in the image no matter what yaw the camera uses.

Those coordinates are a rough composition, not a final layout. Everything downstream is
derived: separate() spaces the models until none intersect, the island is cut to fit where
they ended up, and the camera frames the result. Moving a model needs no other edit.
"""

import sys
import math
import pathlib

import bpy
import numpy
import mathutils

YAW = math.radians(45.0)
PITCH = math.radians(64.0)
ASPECT = 2.4

# (path under assets, u, v, yaw degrees, height above ground, scale)
# Three depth bands with the genres shading left to right. These are a starting
# composition, not final coordinates: separate() below spaces them for real.
CAST = [
    # Back band, the skyline.
    ("PolygonFantasyKingdom/Models/SM_Bld_Preset_House_Archway_01_Optimized", -52, 13, 25, 0, 1.0),
    ("PolygonFantasyKingdom/Models/SM_Bld_Preset_Tower_01_Optimized", -38, 13, 0, 0, 1.0),
    ("POLYGON_NatureBiomes_TropicalJungle_SourceFiles_v2/Tropical_Jungle_SourceFiles/Models/SM_Env_Tree_Pohutukawa_01", -26, 13, 0, 0, 1.0),
    ("POLYGON_Horror_Carnival_SourceFiles_v3/SourceFiles/Models/Props/SM_Prop_Ferris_Wheel_01", -12, 13, 25, 0, 1.0),
    ("POLYGON_Horror_Carnival_SourceFiles_v3/SourceFiles/Models/Props/SM_Prop_Swinging_Chairs_01", 4, 13, 0, 0, 1.0),
    ("POLYGON_City_Source_Files/Models/SM_Bld_OfficeOld_Large_01", 24, 13, -30, 0, 1.0),
    ("POLYGON_Street_Racer_SourceFiles_v3/Models/SM_Veh_ContainerCrane_01_Preset", 44, 13, -15, 0, 1.0),
    ("POLYGON_SciFi_City_SourceFiles_v5/Models/SM_Bld_Large_02", 60, 13, -20, 0, 1.0),

    # Middle band.
    ("PolygonFantasyKingdom/Models/SM_Bld_Preset_Tavern_01_Optimized", -54, 0, -25, 0, 1.0),
    ("PolygonFantasyKingdom/Models/SM_Bld_Preset_Stables_01_Optimized", -34, 0, -15, 0, 1.0),
    ("POLYGON_Horror_Carnival_SourceFiles_v3/SourceFiles/Models/Props/SM_Prop_Tent_Large_01", -16, 0, 0, 0, 1.0),
    ("POLYGON_Western_Frontier_SourceFiles_v4/SourceFiles/Models/Buildings/SM_Bld_Barn_01", 2, 0, -20, 0, 1.0),
    ("POLYGON_War_SourceFiles_v4/Source Files/Models/SM_Bld_TownHouse_01", 18, 0, 15, 0, 1.0),
    ("POLYGON_Nature_Source_Files_v2/Models/SM_Tree_Large_01", 34, 0, 0, 0, 1.0),

    # Front band.
    ("POLYGON_NatureBiomes_MeadowForest_SourceFiles_v2/Meadow_Source_Files/Models/SM_Env_Tree_Meadow_01", -58, -13, 0, 0, 1.0),
    ("POLYGON_Pirate_Pack_SourceFiles_v3/SourceFiles/Models/SM_Bld_Shanty_Preset_03", -38, -13, -30, 0, 1.0),
    ("POLYGON_NatureBiomes_AridDesert_SourceFiles_v2/Arid_Desert_SourceFiles/Models/SM_Env_Rock_Arch_01", -24, -13, 40, 0, 1.0),
    ("POLYGON_Horror_Carnival_SourceFiles_v3/SourceFiles/Models/Props/SM_Prop_Merry_Go_Round_01", -10, -13, 0, 0, 1.0),
    ("POLYGON_Western_Frontier_SourceFiles_v4/SourceFiles/Models/Buildings/SM_Bld_Cabin_01", 6, -13, 25, 0, 1.0),
    ("POLYGON_Construction_SourceFiles_v3/SourceFiles/Models/SK_Veh_Crane_01", 20, -13, 20, 0, 1.0),
    ("POLYGON_Street_Racer_SourceFiles_v3/Models/SM_Veh_Tugboat_01_Preset", 36, -13, -60, 0, 1.0),
    ("POLYGON_SciFi_City_SourceFiles_v5/Models/SM_Veh_Retro_01_Hover", 54, -13, -50, 0, 1.0),

    # In the air, over the shorter rooftops so nothing collides.
    ("PolygonSciFiSpace/Models/SM_Ship_Fighter_03", -48, 15, -60, 25, 1.0),
    ("PolygonSciFiSpace/Models/SM_Ship_Bomber_01", -10, 17, -100, 33, 1.0),
]

# Ground colours as (shadowed, sunlit) pairs, blended by noise so the island does not
# read as one flat slab of plastic next to the textured models.
GRASS = ((0.034, 0.056, 0.018, 1.0), (0.096, 0.134, 0.044, 1.0))
DIRT = ((0.210, 0.140, 0.088, 1.0), (0.360, 0.255, 0.165, 1.0))
ARM_DROP = math.radians(62.0)

# AgX drains colour as luminance climbs, so the render is re-saturated on the way out.
SATURATION = 1.32


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = {"assets": "assets", "out": "docs/images/splash.png", "width": "2200", "samples": "512", "engine": "CYCLES"}
    for index in range(0, len(argv) - 1, 2):
        args[argv[index].lstrip("-")] = argv[index + 1]
    return args


def camera_basis():
    rot = mathutils.Euler((PITCH, 0.0, YAW), "XYZ").to_matrix()
    return rot @ mathutils.Vector((1, 0, 0)), rot @ mathutils.Vector((0, 1, 0)), rot @ mathutils.Vector((0, 0, -1))


def ground_basis():
    """Screen-right and screen-into-the-distance, both flattened onto the ground plane."""
    return (mathutils.Vector((math.cos(YAW), math.sin(YAW), 0.0)),
            mathutils.Vector((-math.sin(YAW), math.cos(YAW), 0.0)))


def rounded_rect(half_u, half_v, steps=72, power=5.0):
    """A superellipse outline, giving the island softened corners rather than a hard box."""
    points = []
    for index in range(steps):
        angle = 2.0 * math.pi * index / steps
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        points.append((math.copysign(abs(cos_a) ** (2.0 / power), cos_a) * half_u,
                       math.copysign(abs(sin_a) ** (2.0 / power), sin_a) * half_v))
    return points


def island_extents(placements, pad=4.0):
    """Fit the ground around wherever the models ended up.

    Measured per side rather than as one symmetric radius: the back band is far deeper
    than the front, so a symmetric island would trail empty grass across the foreground.
    """
    grounded = [item for item in placements if not item["flying"]]
    reach_u = [(item["u"], extent_on(item, (1.0, 0.0))) for item in grounded]
    reach_v = [(item["v"], extent_on(item, (0.0, 1.0))) for item in grounded]
    lo_u = min(centre - half for centre, half in reach_u) - pad
    hi_u = max(centre + half for centre, half in reach_u) + pad
    lo_v = min(centre - half for centre, half in reach_v) - pad
    hi_v = max(centre + half for centre, half in reach_v) + pad
    return (lo_u + hi_u) / 2, (lo_v + hi_v) / 2, (hi_u - lo_u) / 2, (hi_v - lo_v) / 2


def ground_material(name, dark, light):
    """A two-tone noise blend, so the island reads as ground rather than a coloured slab."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.95

    coords = tree.nodes.new("ShaderNodeTexCoord")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.11
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.6
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = dark
    ramp.color_ramp.elements[1].position = 0.70
    ramp.color_ramp.elements[1].color = light

    tree.links.new(coords.outputs["Object"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    return material


def build_island(scene, right, back, centre_u, centre_v, half_u, half_v):
    outline = rounded_rect(half_u, half_v)
    rings = [(1.00, 0.0), (0.98, -2.2), (0.84, -5.4), (0.55, -8.6)]
    # The rings taper toward the island's own centre, so the offset is added after
    # scaling rather than baked into the outline.
    middle = right * centre_u + back * centre_v

    vertices = []
    for scale, height in rings:
        for u, v in outline:
            vertices.append(right * (u * scale) + back * (v * scale) + middle + mathutils.Vector((0, 0, height)))
    tip = len(vertices)
    vertices.append(middle + mathutils.Vector((0, 0, -13.0)))

    count = len(outline)
    faces = [list(range(count))[::-1]]
    for ring in range(len(rings) - 1):
        base = ring * count
        for index in range(count):
            nxt = (index + 1) % count
            faces.append([base + index, base + nxt, base + count + nxt, base + count + index])
    last = (len(rings) - 1) * count
    for index in range(count):
        faces.append([last + index, last + (index + 1) % count, tip])

    mesh = bpy.data.meshes.new("island")
    mesh.from_pydata([tuple(v) for v in vertices], [], faces)
    mesh.update()

    for name, (dark, light) in (("grass", GRASS), ("dirt", DIRT)):
        mesh.materials.append(ground_material(name, dark, light))
    for index, polygon in enumerate(mesh.polygons):
        polygon.material_index = 0 if index == 0 else 1
        polygon.use_smooth = False

    island = bpy.data.objects.new("island", mesh)
    scene.collection.objects.link(island)
    return island


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [obj for obj in bpy.data.objects if obj not in before]


def world_bounds(objects):
    lo = mathutils.Vector((1e18,) * 3)
    hi = mathutils.Vector((-1e18,) * 3)
    for obj in objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            point = obj.matrix_world @ mathutils.Vector(corner)
            lo = mathutils.Vector(map(min, lo, point))
            hi = mathutils.Vector(map(max, hi, point))
    return (None, None) if lo.x > 1e17 else (lo, hi)


def drop_arms(objects):
    """Synty characters import in a T-pose. Swing the upper arms down so they read as idle."""
    armature = next((obj for obj in objects if obj.type == "ARMATURE"), None)
    if armature is None:
        return False
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="POSE")
    for suffix, sign in (("_L", 1.0), ("_R", -1.0)):
        bone = armature.pose.bones.get("UpperArm" + suffix)
        if bone is not None:
            bone.rotation_mode = "XYZ"
            bone.rotation_euler = (0.0, 0.0, ARM_DROP * sign)
    bpy.ops.object.mode_set(mode="OBJECT")
    return True


def footprint_axes(spin, right, back):
    """The model's own two ground axes, expressed in layout coordinates.

    Kept as axes rather than collapsed into a u/v box because most models sit at an
    angle to the layout grid, and the box around a turned rectangle is up to 40 percent
    too big. That slack is what pushes a crowd apart into a sparse field.
    """
    rot = mathutils.Matrix.Rotation(math.radians(spin), 3, "Z")
    axes = []
    for local in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        turned = rot @ mathutils.Vector(local)
        axes.append((turned.dot(right), turned.dot(back)))
    return axes


def extent_on(item, axis):
    """How far the model reaches along a unit axis, from its centre."""
    return (item["half_a"] * abs(axis[0] * item["axis_a"][0] + axis[1] * item["axis_a"][1])
            + item["half_b"] * abs(axis[0] * item["axis_b"][0] + axis[1] * item["axis_b"][1]))


def overlap(first, second, gap):
    """Smallest push that separates two turned rectangles, or None if they are clear.

    Separating axis theorem: if any of the four edge normals shows a gap, the pair does
    not touch. Otherwise the shallowest axis is the cheapest way out.
    """
    if min(first["top"], second["top"]) <= max(first["lift"], second["lift"]):
        return None
    offset = (second["u"] - first["u"], second["v"] - first["v"])
    best = None
    for axis in (first["axis_a"], first["axis_b"], second["axis_a"], second["axis_b"]):
        distance = abs(offset[0] * axis[0] + offset[1] * axis[1])
        depth = extent_on(first, axis) + extent_on(second, axis) + gap - distance
        if depth <= 0.0:
            return None
        if best is None or depth < best[1]:
            direction = -1.0 if offset[0] * axis[0] + offset[1] * axis[1] >= 0.0 else 1.0
            best = ((axis[0] * direction, axis[1] * direction), depth)
    return best


def place(scene, assets, entry, right, back):
    rel, u, v, spin, lift, scale = entry
    path = assets / (rel + ".glb")
    if not path.exists():
        matches = list(assets.glob(f"**/{pathlib.Path(rel).name}.glb"))
        if not matches:
            print(f"MISSING {rel}")
            return None
        path = matches[0]

    objects = import_glb(path)
    posed = drop_arms(objects)
    bpy.context.view_layer.update()
    lo, hi = world_bounds(objects)
    if lo is None:
        print(f"EMPTY   {rel}")
        return None

    holder = bpy.data.objects.new(pathlib.Path(rel).name, None)
    scene.collection.objects.link(holder)
    holder.rotation_euler = (0.0, 0.0, math.radians(spin))
    holder.scale = (scale,) * 3

    # Centre the model on its footprint and sit it on the ground before the holder moves it.
    centre = mathutils.Vector(((lo.x + hi.x) * 0.5, (lo.y + hi.y) * 0.5, lo.z))
    for obj in objects:
        if obj.parent is None:
            obj.parent = holder
            obj.matrix_parent_inverse = mathutils.Matrix.Translation(-centre)

    size = (hi - lo) * scale
    axis_a, axis_b = footprint_axes(spin, right, back)
    print(f"OK      {pathlib.Path(rel).name:<46} {size.x:5.1f} x {size.y:5.1f} x {size.z:5.1f}{'  posed' if posed else ''}")
    return {"holder": holder, "u": float(u), "v": float(v), "lift": float(lift),
            "axis_a": axis_a, "axis_b": axis_b, "half_a": size.x / 2, "half_b": size.y / 2,
            "top": lift + size.z, "mass": size.x * size.y, "flying": lift > 0.0}


def separate(placements, gap=0.8, rounds=600):
    """Nudge overlapping models apart until nothing intersects.

    Hand-placed coordinates are a composition, not a packing, so models sink into each
    other. A pair only truly clashes when it overlaps on all three axes, which is why the
    height test comes first: a ship flying over a rooftop is not a collision. Each clash
    is resolved along its shallowest axis, and the larger model yields less, so the big
    landmarks hold the composition while small props give way around them.
    """
    for _ in range(rounds):
        worst = 0.0
        for index, first in enumerate(placements):
            for second in placements[index + 1:]:
                found = overlap(first, second, gap)
                if found is None:
                    continue
                axis, depth = found
                worst = max(worst, depth)
                total = first["mass"] + second["mass"] or 1.0
                first["u"] += axis[0] * depth * (second["mass"] / total)
                first["v"] += axis[1] * depth * (second["mass"] / total)
                second["u"] -= axis[0] * depth * (first["mass"] / total)
                second["v"] -= axis[1] * depth * (first["mass"] / total)
        if worst < 1e-3:
            break

    clashes = sum(1 for index, first in enumerate(placements)
                  for second in placements[index + 1:] if overlap(first, second, 0.0))
    print(f"spaced  {len(placements)} models, {clashes} remaining intersections")
    return clashes


def apply_layout(placements, right, back):
    for item in placements:
        item["holder"].location = right * item["u"] + back * item["v"] + mathutils.Vector((0, 0, item["lift"]))


def add_lighting(scene):
    key_data = bpy.data.lights.new("key", "SUN")
    key_data.energy = 6.5
    key_data.angle = math.radians(2.5)
    key_data.color = (1.0, 0.96, 0.88)
    key = bpy.data.objects.new("key", key_data)
    key.rotation_euler = (math.radians(50.0), 0.0, math.radians(-30.0))
    scene.collection.objects.link(key)

    fill_data = bpy.data.lights.new("fill", "SUN")
    fill_data.energy = 0.25
    fill_data.angle = math.radians(45.0)
    fill_data.color = (0.90, 0.93, 1.0)
    fill = bpy.data.objects.new("fill", fill_data)
    fill.rotation_euler = (math.radians(62.0), 0.0, math.radians(155.0))
    scene.collection.objects.link(fill)

    # Synty atlases carry metallic 0.5 across the whole surface, so a flat grey world
    # leaves every roof and hull reflecting nothing but grey. A real sky gives them a
    # blue zenith and a warm horizon to pick up, which is what stops it reading as clay.
    # The island's underside faces away from sky and sun alike, so without a bounce it
    # renders as a black wedge that vanishes against a dark README background.
    bounce_data = bpy.data.lights.new("bounce", "SUN")
    bounce_data.energy = 0.9
    bounce_data.angle = math.radians(60.0)
    bounce_data.color = (1.0, 0.90, 0.76)
    bounce = bpy.data.objects.new("bounce", bounce_data)
    bounce.rotation_euler = (math.radians(165.0), 0.0, math.radians(30.0))
    scene.collection.objects.link(bounce)

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    tree = world.node_tree
    sky = tree.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(40.0)
    sky.sun_rotation = math.radians(240.0)
    sky.sun_disc = False
    sky.altitude = 200.0
    # Thinner air and a little dust: full Rayleigh scattering tints every shadow blue,
    # which the saturation pass then exaggerates into teal.
    sky.air_density = 0.55
    sky.aerosol_density = 2.2
    background = tree.nodes["Background"]
    background.inputs[1].default_value = 0.45
    tree.links.new(sky.outputs[0], background.inputs[0])
    scene.world = world


def grade(scene, saturation):
    """Lift saturation before the view transform.

    AgX desaturates progressively as luminance climbs, so the atlases arrive at the
    display end paler than they were authored. Correcting it here, on the rendered image,
    keeps the converted materials themselves untouched.
    """
    group = bpy.data.node_groups.new("grade", "CompositorNodeTree")
    group.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")

    # Blender 5's compositor reads the render through a Render Layers node inside the
    # group; feeding a Group Input instead silently renders nothing at all.
    layers = group.nodes.new("CompositorNodeRLayers")
    saturate = group.nodes.new("CompositorNodeHueSat")
    saturate.inputs["Saturation"].default_value = saturation
    result = group.nodes.new("NodeGroupOutput")

    group.links.new(layers.outputs["Image"], saturate.inputs["Image"])
    group.links.new(saturate.outputs["Image"], result.inputs[0])
    scene.compositing_node_group = group


def fit_camera(scene, margin=1.04):
    """Frame everything that exists, so the layout can change without retuning the camera.

    Measured on real vertices rather than bounding boxes: the island's axis-aligned box
    juts well past its rounded outline, which would leave a wide band of empty alpha.
    """
    bpy.context.view_layer.update()
    right, up, forward = camera_basis()
    axis_x, axis_y = numpy.array(right), numpy.array(up)
    bounds = None

    for obj in scene.objects:
        if obj.type != "MESH" or not obj.data.vertices:
            continue
        flat = numpy.empty(len(obj.data.vertices) * 3, dtype=numpy.float32)
        obj.data.vertices.foreach_get("co", flat)
        matrix = numpy.array(obj.matrix_world)
        world = flat.reshape(-1, 3).astype(numpy.float64) @ matrix[:3, :3].T + matrix[:3, 3]
        screen_x, screen_y = world @ axis_x, world @ axis_y
        extent = numpy.array([screen_x.min(), screen_x.max(), screen_y.min(), screen_y.max()])
        bounds = extent if bounds is None else numpy.array([
            min(bounds[0], extent[0]), max(bounds[1], extent[1]),
            min(bounds[2], extent[2]), max(bounds[3], extent[3])])

    centre_x, centre_y = (bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2
    span = max(bounds[1] - bounds[0], (bounds[3] - bounds[2]) * ASPECT) * margin

    data = bpy.data.cameras.new("camera")
    data.type = "ORTHO"
    data.ortho_scale = span
    data.clip_start = 1.0
    data.clip_end = 4000.0
    camera = bpy.data.objects.new("camera", data)
    camera.rotation_euler = (PITCH, 0.0, YAW)
    camera.location = right * centre_x + up * centre_y - forward * 900.0
    scene.collection.objects.link(camera)
    scene.camera = camera
    print(f"framed  ortho_scale={span:.1f}")


def main():
    args = parse_args()
    assets = pathlib.Path(args["assets"]).resolve()
    out = pathlib.Path(args["out"]).resolve()
    width = int(args["width"])

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    right, back = ground_basis()

    placements = [item for item in (place(scene, assets, entry, right, back) for entry in CAST) if item]
    separate(placements)
    apply_layout(placements, right, back)
    build_island(scene, right, back, *island_extents(placements))

    add_lighting(scene)
    grade(scene, SATURATION)
    fit_camera(scene)

    scene.render.engine = args["engine"]
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = int(args["samples"])
        scene.cycles.use_denoising = True
    # AgX rolls highlights off instead of clipping them, which is what lets the scene take
    # a hard key light without the roofs blowing out. Left flat it also drains the colour,
    # so the Punchy look puts the contrast and saturation back at the display end rather
    # than by tampering with the converted materials.
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"
    scene.view_settings.exposure = 0.15
    scene.render.filter_size = 1.1
    scene.render.resolution_x = width
    scene.render.resolution_y = int(width / ASPECT)
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.compression = 100
    scene.render.filepath = str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    print(f"wrote {out} at {width}x{int(width / ASPECT)} via {scene.render.engine}")


main()
