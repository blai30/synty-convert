"""Material manifest records: what a converted material becomes for Godot.

The converter stops at JSON and never writes a ``.tres``, so that Godot generates every
resource id and uid itself. Alongside each observed material, a sibling record is written
for every other member of its flavor set, so a model can be re-skinned in Godot by pointing
it at a different generated resource.

Pure Python: imported by the CLI and by ``audit.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import material_flavors
import material_names

# Keys apply_channels owns. Cleared before rewriting, so a sibling copied from its base
# cannot keep a glow the pack authored no companion for.
CHANNEL_KEYS = ("emission_texture", "emission_color", "emission_energy",
                "emission_reference", "emission_match",
                "normal_texture", "normal_scale", "normal_reference", "normal_match")


def linear_to_srgb(value):
    """Convert a linear color channel to the sRGB space Godot's albedo_color expects.

    glTF stores base color linearly. Writing the linear number straight into a .tres
    renders noticeably too dark.
    """
    if value <= 0.0031308:
        return round(value * 12.92, 6)
    return round(1.055 * (value ** (1 / 2.4)) - 0.055, 6)


def as_res_path(path, output_root, res_prefix):
    """Where a converted file will live in the Godot project it gets copied into."""
    relative = Path(path).resolve().relative_to(output_root)
    return f"{res_prefix.rstrip('/')}/" + str(relative).replace(os.sep, "/")


def channel_of(entry, name):
    """One resolved texture channel of a material record, or an empty one."""
    return (entry.get("channels") or {}).get(name) or {}


def is_diagnostic(key):
    """True for a key recording how an observed material's own reference resolved.

    A generated sibling resolved nothing against an FBX of its own, so it carries none of
    these. Shared with audit.py so the two cannot disagree about which keys those are.
    """
    return key in ("reference", "match") or key.endswith(("_reference", "_match"))


def apply_channels(record, entry, output_root, res_prefix):
    """Write the emission and normal keys a material's channels imply onto a record."""
    for key in CHANNEL_KEYS:
        record.pop(key, None)
    emission = channel_of(entry, "emission")
    normal = channel_of(entry, "normal")
    if emission.get("texture"):
        # A connected file stands in for the emissive color, the way Maya treats one.
        record["emission_texture"] = as_res_path(emission["texture"], output_root, res_prefix)
        record["emission_energy"] = entry["emission_strength"]
    elif any(entry["emission_color"]):
        record["emission_color"] = [linear_to_srgb(c) for c in entry["emission_color"]]
        record["emission_energy"] = entry["emission_strength"]
    if normal.get("texture"):
        record["normal_texture"] = as_res_path(normal["texture"], output_root, res_prefix)
        record["normal_scale"] = entry["normal_strength"]
    return record


def manifest_record(name, entry, output_root, res_prefix):
    """One observed material, as the Godot generator will read it."""
    albedo = channel_of(entry, "albedo")
    record = {"name": name, "used_by": entry["used_by"],
              "source_names": sorted(entry["sources"])}
    if albedo.get("texture"):
        record["albedo_texture"] = as_res_path(albedo["texture"], output_root, res_prefix)
    else:
        record["albedo_color"] = [*(linear_to_srgb(c) for c in entry["color"]), entry["alpha"]]
    apply_channels(record, entry, output_root, res_prefix)
    record["roughness"] = entry["roughness"]
    record["metallic"] = entry["metallic"]
    if entry.get("transparency"):
        record["transparency"] = entry["transparency"]
    for channel in ("albedo", "emission", "normal", "alpha"):
        asked = channel_of(entry, channel)
        # A mask naming the file the material is colored with says nothing new.
        if not asked.get("reference") or (channel == "alpha"
                                          and asked["reference"] == albedo.get("reference")):
            continue
        prefix = "" if channel == "albedo" else channel + "_"
        record[prefix + "reference"] = asked["reference"]
        record[prefix + "match"] = asked.get("method")
    return record


def sibling_channels(entry, member, companions, output_root, pack, warnings):
    """The channels a sibling wearing ``member`` would have had.

    Companions are keyed on the atlas, so a sibling takes its own member's maps rather than
    inheriting the observed material's. A companion that was never mirrored into the output
    is dropped and warned about: a manifest must not name a file that is not on disk.
    """
    channels = dict(entry.get("channels") or {})
    original_albedo = channels.get("albedo")
    channels["albedo"] = dict(original_albedo or {}, member=member, texture_source=member)
    # A cutout binds one image as both color and mask, so the mask follows the new albedo.
    if channels.get("alpha") is original_albedo:
        channels["alpha"] = channels["albedo"]
    declared = companions.get(member) or {}
    for channel in material_flavors.COMPANION_CHANNELS:
        target = declared.get(channel)
        mirrored = Path(output_root) / pack / target if target else None
        if mirrored is not None and mirrored.exists():
            channels[channel] = {"member": target, "texture_source": target,
                                 "texture": str(mirrored), "reference": None,
                                 "method": "companion", "score": None}
            continue
        channels.pop(channel, None)
        if target:
            warnings.append(f"{pack}: companion {channel} for {member} skipped, "
                            f"{target} was not mirrored into the output")
    return channels


def flavor_variants(record, entry, sets, companions, output_root, pack, res_prefix, warnings):
    """Sibling records for the rest of the flavor set this material's texture belongs to.

    Generated per observed material rather than per set, so qualifiers carry over:
    PolygonFantasyKingdom_01_A_R75_M50 yields _01_B_R75_M50. Siblings are named by the same
    function that named the observed material, so the two halves of the tool cannot drift
    on what a given set of channels is called.
    """
    member = channel_of(entry, "albedo").get("member")
    if not member or not sets:
        return []
    if not record["name"].startswith(Path(member).stem):
        # Not named after its atlas, so there is no safe way to rename it for a sibling.
        return []
    siblings = []
    for other in material_flavors.variants_of(member, sets):
        target = Path(output_root) / pack / other
        if not target.exists():
            warnings.append(f"{pack}: flavor variant {Path(other).stem} skipped, "
                            f"{other} was not mirrored into the output")
            continue
        as_observed = dict(entry, channels=sibling_channels(entry, other, companions,
                                                            output_root, pack, warnings))
        sibling = {key: value for key, value in record.items() if not is_diagnostic(key)}
        sibling["name"] = material_names.canonical_name(as_observed, companions)
        sibling["used_by"] = 0
        sibling["variant_of"] = record["name"]
        sibling["source_names"] = []
        sibling["albedo_texture"] = as_res_path(target, output_root, res_prefix)
        apply_channels(sibling, as_observed, output_root, res_prefix)
        siblings.append(sibling)
    return siblings


def merge_sibling(by_name, sibling, pack, warnings):
    """Add a generated sibling unless an observed material already holds its name.

    Two observed materials in one set can want the same sibling name. That only costs
    something when the two would render differently; when they are identical apart from
    which base produced them, dropping either is free.
    """
    existing = by_name.get(sibling["name"])
    if existing is None:
        by_name[sibling["name"]] = sibling
    elif existing.get("variant_of"):
        differs = {key for key in set(existing) | set(sibling)
                   if key != "variant_of" and existing.get(key) != sibling.get(key)}
        if differs:
            warnings.append(f"{pack}: flavor variant {sibling['name']} generated twice with "
                            f"differing {', '.join(sorted(differs))}, from "
                            f"{existing['variant_of']} and {sibling['variant_of']}; "
                            f"keeping the first")


def write_manifests(totals, materials_root, output_root, res_prefix, contexts):
    """Write one manifest per pack for the Godot material generator to consume."""
    written = []
    # Not totals.warnings: report() has already printed by the time main() calls this.
    warnings = []
    for pack, materials in sorted(totals.materials.items()):
        if not pack or not materials:
            continue
        observed = [(manifest_record(name, entry, output_root, res_prefix), entry)
                    for name, entry in sorted(materials.items())]
        by_name = {record["name"]: record for record, _ in observed}
        declared = ((contexts.get(pack) or {}).get("materials") or {})
        sets = declared.get("sets") or {}
        companions = declared.get("companions") or {}
        for record, entry in observed:
            for sibling in flavor_variants(record, entry, sets, companions, output_root,
                                           pack, res_prefix, warnings):
                merge_sibling(by_name, sibling, pack, warnings)
        target = materials_root / pack / "materials.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        entries = [by_name[name] for name in sorted(by_name)]
        target.write_text(json.dumps({"pack": pack, "materials": entries}, indent=2) + "\n",
                          encoding="utf-8")
        written.append((target, len(entries)))
    if warnings:
        print(f"\n{len(warnings)} flavor variant warning(s):")
        for warning in warnings[:15]:
            print(f"  {warning}")
        if len(warnings) > 15:
            print(f"  ... and {len(warnings) - 15} more")
    return written
