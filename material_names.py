"""How a converted material is named.

Materials are named for their atlas, since Synty's own names are Maya leftovers that are
ambiguous across files: lambert1 alone maps to four textures. Every property that could make
two materials on one atlas render differently then adds a qualifier, so one name means one
thing across a whole pack. A companion map is the exception: it is declared per atlas, so
the base name already implies it.

Both halves of the tool emit names, the worker for what it observed and the CLI for the
siblings it generates, so the rule lives here where they share it.

Pure Python: this module is imported both by the Blender worker and by the CLI.
"""

from __future__ import annotations

import math
import os
import re

# What Blender's FBX importer produces for a material declaring no shading properties at
# all, which is most of them: FBX Shininess defaults to 20 and Blender reads it as
# roughness = 1 - sqrt(shininess) / 10. Derived rather than written out, so a material that
# says nothing is never mistaken for one that asked for this exact value.
DEFAULT_ROUGHNESS = round(1.0 - math.sqrt(20.0) / 10.0, 4)


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


def map_qualifier(channel, base, declared):
    """What a bound map adds to a name, which is nothing when the atlas already implied it.

    Compared against ``declared``, the map the atlas itself carries, rather than against a
    mark on the channel: what decides a name is whether the map could differ between two
    materials on one atlas, never who asked for it. An unresolved reference has no member
    and so always qualifies, which is the one case that does leave a material without the
    map the rest of its atlas wears.
    """
    if declared and channel.get("member") == declared:
        return None
    return unshared_tail(stem_of(channel), base)


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


def canonical_name(record, companions=None):
    """A stable name shared by every mesh whose material is identical.

    Qualifiers come from the material itself and never from the order files happen to be
    converted in, so one name means one thing across a whole pack.
    """
    channels = record["channels"]
    base = base_name(record)
    # What this material's atlas declares, which is what its maps are measured against.
    declared = (companions or {}).get((channels.get("albedo") or {}).get("member")) or {}
    parts = [base]

    if channels.get("emission"):
        parts.append(map_qualifier(channels["emission"], base, declared.get("emission")))
    elif any(record["emission_color"]):
        # Only where no map is bound: build_material discards a declared emission color the
        # moment a map covers it, so naming one there would name something nothing renders.
        parts.append("Emissive" + hex_of(record["emission_color"]))
    if channels.get("normal"):
        parts.append(map_qualifier(channels["normal"], base, declared.get("normal")))
    if channels.get("alpha"):
        parts.append("Cutout")
    elif record["alpha"] < 0.999:
        parts.append("A%02d" % round(record["alpha"] * 100))
    if record["roughness"] != DEFAULT_ROUGHNESS:
        parts.append("R%02d" % round(record["roughness"] * 100))
    if record["metallic"]:
        parts.append("M%02d" % round(record["metallic"] * 100))
    if not channels.get("albedo"):
        # Nothing above names the color, and color is all an untextured material is.
        parts.append(hex_of(record["color"]))
    return re.sub(r"_+", "_", "_".join(part for part in parts if part)).strip("_")
