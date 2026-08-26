"""Audit converted GLB files and the material manifests that go with them.

Checks the properties the converter is supposed to guarantee, reading the files straight
off disk rather than trusting the conversion log:

    python audit.py [--dst assets] [--materials-dir materials]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import struct
import sys
import urllib.parse
from pathlib import Path

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A

# A level of a vendor LOD chain. Every level ships as an ordinary mesh, so a model that
# still holds more than one of them renders all of them at once.
LOD_SUFFIX = re.compile(r"^(?P<base>.+)_LOD(?P<level>\d+)$", re.IGNORECASE)


def gltf_of(path):
    with open(path, "rb") as handle:
        magic, version, _ = struct.unpack("<III", handle.read(12))
        if magic != GLB_MAGIC or version != 2:
            raise ValueError("not a glTF 2.0 binary file")
        length, kind = struct.unpack("<II", handle.read(8))
        if kind != CHUNK_JSON:
            raise ValueError("first chunk is not JSON")
        return json.loads(handle.read(length))


def audit_models(root, failures, stats):
    for path in sorted(root.rglob("*.glb")):
        stats["models"] += 1
        try:
            gltf = gltf_of(path)
        except ValueError as error:
            failures[f"unreadable: {error}"].append(str(path))
            continue

        if gltf.get("extensionsRequired"):
            failures["requires a glTF extension Godot cannot load"].append(str(path))

        for node in (gltf["nodes"][i] for i in gltf["scenes"][gltf.get("scene", 0)]["nodes"]):
            if "scale" in node or "rotation" in node:
                failures["root node is not identity"].append(f"{path}: {node.get('name')}")

        for image in gltf.get("images", []):
            stats["images"] += 1
            if "bufferView" in image:
                # An embedded image duplicates the atlas into every model that uses it.
                failures["image is embedded rather than referenced"].append(str(path))
            uri = image.get("uri")
            if not uri:
                failures["image has no uri"].append(str(path))
                continue
            target = (path.parent / urllib.parse.unquote(uri)).resolve()
            if not target.exists():
                failures["image uri does not resolve"].append(f"{path} -> {uri}")
            else:
                stats["textures"].add(str(target))

        chains = collections.defaultdict(set)
        for node in gltf.get("nodes", []):
            match = LOD_SUFFIX.match(node.get("name") or "")
            if match and node.get("mesh") is not None:
                chains[match.group("base")].add(int(match.group("level")))
        if any(len(levels) > 1 for levels in chains.values()):
            # Not a failure: --lods keep asks for this deliberately.
            stats["models_with_stacked_lods"] += 1

        for mesh in gltf.get("meshes", []):
            for primitive in mesh["primitives"]:
                if "TEXCOORD_0" not in primitive["attributes"]:
                    failures["mesh lost its UVs"].append(str(path))
                if "material" not in primitive:
                    stats["primitives_without_material"] += 1


def on_disk(reference, res_prefix, dst):
    """Map a res:// path from a manifest back to the converted file it names.

    The converter is not a Godot project, so res:// only makes sense relative to where
    the output is destined to land; everything under the prefix mirrors the output tree.
    """
    prefix = res_prefix.rstrip("/") + "/"
    if not reference.startswith(prefix):
        return None
    return dst / reference[len(prefix):]


def audit_manifests(root, dst, res_prefix, failures, stats):
    for path in sorted(root.rglob("materials.json")):
        stats["manifests"] += 1
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for entry in manifest["materials"]:
            stats["materials"] += 1
            if not entry.get("albedo_texture") and not entry.get("albedo_color"):
                failures["material has neither texture nor colour"].append(f"{path}: {entry['name']}")
            if not entry.get("albedo_texture"):
                stats["materials_without_texture"] += 1
            for key in ("albedo_texture", "emission_texture", "normal_texture"):
                texture = entry.get(key)
                if not texture:
                    continue
                stats[key] += 1
                resolved = on_disk(texture, res_prefix, dst)
                if resolved is None:
                    failures[f"manifest texture is not under {res_prefix}"].append(f"{path}: {texture}")
                elif not resolved.exists():
                    failures["manifest texture does not exist"].append(f"{path}: {texture}")
        # .tres files only exist once the user has run the generator in their project.
        for tres in path.parent.glob("*.tres"):
            stats["tres"] += 1
            for line in tres.read_text(encoding="utf-8").splitlines():
                if line.startswith("[ext_resource"):
                    reference = line.split('path="', 1)[1].split('"', 1)[0]
                    resolved = on_disk(reference, res_prefix, dst)
                    if resolved is not None and not resolved.exists():
                        failures["tres references a missing texture"].append(f"{tres}: {reference}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent
    parser.add_argument("--dst", type=Path, default=root / "assets")
    parser.add_argument("--materials-dir", type=Path, default=root / "materials")
    parser.add_argument("--res-prefix", default=None,
                        help="res:// location the assets are destined for "
                             "(default: res://<output folder name>)")
    args = parser.parse_args()

    res_prefix = args.res_prefix or f"res://{args.dst.name}"
    failures = collections.defaultdict(list)
    stats = collections.Counter()
    stats["textures"] = set()

    if args.dst.is_dir():
        audit_models(args.dst, failures, stats)
    if args.materials_dir.is_dir():
        audit_manifests(args.materials_dir, args.dst, res_prefix, failures, stats)

    print(f"models {stats['models']}, image references {stats['images']} "
          f"pointing at {len(stats['textures'])} distinct texture files")
    print(f"manifests {stats['manifests']}, materials {stats['materials']} "
          f"({stats['materials_without_texture']} colour only), tres {stats['tres']}")
    if stats["emission_texture"] or stats["normal_texture"]:
        print(f"extra maps: {stats['emission_texture']} emission, {stats['normal_texture']} normal")
    if stats["primitives_without_material"]:
        print(f"primitives with no material: {stats['primitives_without_material']}")
    if stats["models_with_stacked_lods"]:
        print(f"models shipping a whole LOD chain: {stats['models_with_stacked_lods']} "
              f"(every level renders at once; converted with --lods keep)")

    if not failures:
        print("\nPASS: no embedded images, every uri resolves, roots identity, UVs intact")
        return 0
    print()
    for reason, items in failures.items():
        print(f"FAIL {reason}: {len(items)}")
        for item in items[:5]:
            print(f"   {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
