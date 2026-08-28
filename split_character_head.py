"""Split a Synty character's head off its body, executed inside Blender.

Imported by ``blender_convert.py`` and applied between import and export, so both halves
flow through the same normalization, material and export path as any other mesh and land
in a single GLB as two sibling nodes under one skeleton.

A Synty body is one contiguous mesh with one material, so the head cannot be found by
material or by loose part. Selection is by vertex weight instead: a vertex moves to the
head when the head bone *and everything parented under it* holds most of its weight. The
descendants are what matters in practice. Eyes and eyebrows hang off the head as their
own bones and carry the eye geometry's weights, so selecting on the head bone alone
leaves eyeballs floating where the head used to be.

Faces spanning the boundary stay with the body, so the seam vertices exist in both halves
at the same positions with the same weights and the neck cannot crack under a pose. That
leaves the body open at the neck: invisible while the head is shown, a hole straight
through the torso once it is hidden. So the body is capped and the head is not. The
head's opening sits inside the body's cap, and capping both would put two coincident
faces in one place to z-fight.
"""

from __future__ import annotations

import collections

import bmesh
import bpy
from mathutils import Vector

HEAD_BONE = "head"
HEAD_WEIGHT = 0.5
COORDINATE_PRECISION = 5


def find_head_bone(armature):
    """The head bone, matched case-insensitively.

    Synty ships two rig families and they disagree on capitalization: the older packs name
    it ``Head``, the newer UE-style ones ``head``. An exact match also keeps this off
    ``headAttach``, which is a child and arrives through the closure below anyway.
    """
    for bone in armature.data.bones:
        if bone.name.lower() == HEAD_BONE:
            return bone
    return None


def head_group_indices(obj, head_bone):
    names = {head_bone.name} | {bone.name for bone in head_bone.children_recursive}
    return {group.index for group in obj.vertex_groups if group.name in names}


def skinned_meshes(armature):
    """Meshes the armature actually deforms.

    Parenting alone is not enough: Synty packs park static props under the rig, and those
    have no armature modifier and no business being split.
    """
    return [obj for obj in bpy.data.objects
            if obj.type == "MESH" and obj.parent is armature
            and any(modifier.type == "ARMATURE" for modifier in obj.modifiers)]


def head_fraction(vertex, deform, indices):
    """How much of a vertex's weight sits on the head, as a fraction of its total."""
    weights = vertex[deform]
    total = sum(weights.values())
    if total <= 0.0:
        return 0.0
    return sum(weight for group, weight in weights.items() if group in indices) / total


def edge_key(edge):
    """A position-based identity for an edge, stable across the separate.

    Separating renumbers vertices and splits the seam into two meshes, so indices cannot
    carry across it. Nothing moves, so coordinates can.
    """
    return frozenset(tuple(round(value, COORDINATE_PRECISION) for value in vertex.co)
                     for vertex in edge.verts)


def seam_edges(edit):
    """Coordinate keys for the edges where a head face meets a body face.

    This is the ring the separate is about to open up, recorded before it happens so the
    cap can target exactly it and leave vendor-authored openings alone. Synty eye sockets
    are open cups, and filling those would put a lid over both eyes.
    """
    keys = set()
    for edge in edit.edges:
        head_faces = sum(1 for face in edge.link_faces if all(v.select for v in face.verts))
        if head_faces and head_faces < len(edge.link_faces):
            keys.add(edge_key(edge))
    return keys


def boundary_loops(boundary):
    """Group boundary edges into the rings they form."""
    incident = collections.defaultdict(list)
    for edge in boundary:
        for vertex in edge.verts:
            incident[vertex].append(edge)
    loops, visited = [], set()
    for edge in boundary:
        if edge in visited:
            continue
        loop, stack = [], [edge]
        visited.add(edge)
        while stack:
            current = stack.pop()
            loop.append(current)
            for vertex in current.verts:
                for neighbor in incident[vertex]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
        loops.append(loop)
    return loops


def ordered_ring(edges):
    """Walk a set of boundary edges into vertex order, or None if it is not a simple ring."""
    incident = collections.defaultdict(list)
    for edge in edges:
        for vertex in edge.verts:
            incident[vertex].append(edge)
    if any(len(touching) != 2 for touching in incident.values()):
        return None
    start = next(iter(incident))
    ring, previous, current = [start], None, start
    while len(ring) <= len(edges):
        edge = next(e for e in incident[current] if e is not previous)
        following = edge.other_vert(current)
        if following is start:
            return ring if len(ring) == len(edges) else None
        ring.append(following)
        previous, current = edge, following
    return None


def fan_fill(mesh, edges, ring, deform, uv_layer):
    """Close one ring with a triangle fan around a new center vertex.

    Spanning the ring with a single face instead, which is what ``holes_fill`` and the
    glTF exporter both reach for, means choosing diagonals between vertices already on the
    rim. A Synty neck is pinched enough that one of those diagonals is an edge the body
    already has, and reusing it lands a third face on that edge. Every edge a fan adds
    ends at the new center vertex, so it cannot collide with anything already there, and
    the ring is closed by construction rather than by whatever a fill heuristic manages.

    The center vertex takes the ring's mean weights, so the cap deforms with the neck
    instead of hanging in place when the character moves.
    """
    center = mesh.verts.new(sum((vertex.co for vertex in ring), Vector()) / len(ring))
    weights = collections.defaultdict(float)
    for vertex in ring:
        for group, weight in vertex[deform].items():
            weights[group] += weight
    for group, weight in weights.items():
        center[deform][group] = weight / len(ring)

    corners = {}
    if uv_layer is not None:
        # Every rim vertex sits on neck skin, and Synty atlases are flat color patches,
        # so the whole cap lands on the one texel the neck already uses.
        for vertex in ring:
            corners[vertex] = next(iter(vertex.link_loops))[uv_layer].uv.copy()
        corners[center] = sum(corners.values(), Vector((0.0, 0.0))) / len(corners)

    material = edges[0].link_faces[0].material_index
    created = 0
    for edge in edges:
        # Wind against the face already on this edge so the cap agrees with the shell it
        # closes, and so it faces the camera that replaced the head rather than the chest.
        loop = edge.link_loops[0]
        face = mesh.faces.new((loop.link_loop_next.vert, loop.vert, center))
        face.material_index = material
        if uv_layer is not None:
            for corner in face.loops:
                corner[uv_layer].uv = corners[corner.vert]
        face.normal_update()
        created += 1
    return created


def cap(obj, seam, warnings):
    """Close the neck opening the separate left behind.

    Returns the face count and how many rings could not be closed.

    Whole boundary loops are closed, chosen by containing at least one seam edge, rather
    than the seam edges alone. A neck ring is not always made only of edges the separate
    created: where the vendor mesh was already open near the collar, the ring comes out
    part new and part pre-existing, and treating only the new part as the hole leaves an
    unclosed arc. Requiring a seam edge is what leaves vendor openings elsewhere alone, so
    the eye cups keep their opening rather than getting a lid.
    """
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    deform = mesh.verts.layers.deform.verify()
    uv_layer = mesh.loops.layers.uv.active
    opened = [loop for loop in boundary_loops([edge for edge in mesh.edges
                                               if len(edge.link_faces) == 1])
              if any(edge_key(edge) in seam for edge in loop)]

    faces, unclosed = 0, 0
    for loop in opened:
        ring = ordered_ring(loop)
        if ring is None:
            # Branching boundary, which means the vendor mesh was already non-manifold
            # around the neck. A partly closed neck shows through the torso and nobody
            # sees it until a head is hidden in game, so it gets counted and reported
            # rather than passing for a cap.
            unclosed += 1
            warnings.append(f"'{obj.name}' neck opening is not a simple ring; left open")
            continue
        faces += fan_fill(mesh, loop, ring, deform, uv_layer)

    if faces:
        mesh.verts.index_update()
        mesh.to_mesh(obj.data)
        obj.data.update()
    mesh.free()
    return faces, unclosed


def separate_head(obj, indices, warnings):
    """Move the head faces into an object of their own and return it, or None.

    Selection goes through bmesh in edit mode rather than ``mesh.vertices[i].select`` in
    object mode. The object mode flags do not survive the mode switch: the mesh arrives
    from the importer fully selected, so separating on those moves every face into the new
    object and leaves the original empty, which looks like a split and is not one.
    """
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    existing = set(bpy.context.scene.objects)
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")

    edit = bmesh.from_edit_mesh(obj.data)
    deform = edit.verts.layers.deform.verify()
    for vertex in edit.verts:
        vertex.select_set(head_fraction(vertex, deform, indices) >= HEAD_WEIGHT)

    # Counted in faces, not vertices, because faces are what separate actually moves.
    # A modular body that already ships headless still carries a stray head-weighted
    # vertex or two, and separating on those alone yields an object with no faces: the
    # exporter drops it, and the body is left renamed for a split that never happened.
    faces = sum(1 for face in edit.faces if all(vertex.select for vertex in face.verts))
    if not faces or faces == len(edit.faces):
        bpy.ops.object.mode_set(mode="OBJECT")
        return None, set()

    seam = seam_edges(edit)
    # Selected vertices leave the faces between them unselected, and separate works on
    # faces.
    edit.select_flush(True)
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")

    created = [scene_object for scene_object in bpy.context.scene.objects
               if scene_object not in existing]
    if len(created) != 1:
        warnings.append(f"'{obj.name}' separated into {len(created)} objects, expected 1")
        return None, set()
    return created[0], seam


def split_scene(warnings):
    """Split every skinned mesh in the scene that has a head, and describe what happened.

    Returns one record per mesh split, and an empty list for the overwhelming majority of
    files that are props rather than characters. Detection is by content, not filename: a
    single armature carrying a head bone is what makes a file a character here.
    """
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        return []
    armature = armatures[0]
    head_bone = find_head_bone(armature)
    if head_bone is None:
        return []
    bones = {bone.name for bone in armature.data.bones}

    records = []
    for obj in skinned_meshes(armature):
        indices = head_group_indices(obj, head_bone)
        if not indices:
            continue
        base = obj.name
        # glTF node names and bone names share one namespace, so a mesh called Head makes
        # the exporter rename the *bone* Head to Head_2 and every bone map downstream
        # stops recognizing the rig. Blender cannot see this coming, since objects and
        # bones are separate namespaces there.
        collisions = {f"{base}_Body", f"{base}_Head"} & bones
        if collisions:
            warnings.append(f"'{base}' would collide with bone {sorted(collisions)[0]}; not split")
            continue

        head, seam = separate_head(obj, indices, warnings)
        if head is None:
            continue
        head.name = f"{base}_Head"
        obj.name = f"{base}_Body"
        faces, unclosed = cap(obj, seam, warnings)
        records.append({
            "mesh": base,
            "head_vertices": len(head.data.vertices),
            "body_vertices": len(obj.data.vertices),
            "cap_faces": faces,
            "open_rings": unclosed,
        })
    return records
