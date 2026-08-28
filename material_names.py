"""How a converted material is named.

Materials are named for their atlas, since Synty's own names are Maya leftovers that are
ambiguous across files: lambert1 alone maps to four textures. Every property that would make
two materials render differently then adds a qualifier, so one name means one thing across a
whole pack.

The rule lives here rather than in the Blender worker because both halves of the tool emit
material names. The worker names what it observed in an FBX, and ``flavor_variants`` in the
CLI names the siblings it generates for the rest of a flavor set. Those two are obliged to
agree, and sharing the function is the only mechanism that makes them agree rather than the
CLI slicing strings off a name the worker produced.

Pure Python: this module is imported both by the Blender worker and by the CLI.
"""

from __future__ import annotations

import math
import os
import re

# What Blender's FBX importer produces for a material that declares no shading properties at
# all, which is 93% of the corpus: FBX Shininess defaults to 20 and Blender converts it with
# roughness = 1 - sqrt(shininess) / 10. Derived rather than written out so a material that
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
        # Nothing above names the color, and color is all an untextured material is.
        parts.append(hex_of(record["color"]))
    return re.sub(r"_+", "_", "_".join(part for part in parts if part)).strip("_")
