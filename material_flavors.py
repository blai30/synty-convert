"""Curated material knowledge the FBX does not carry, read out of material_overrides.json.

Two kinds live here.

**Flavor sets** are the textures a pack ships as alternatives for one surface: five tileable
walls, or three recolors of one atlas. The choice belongs to the consumer, so the FBX names
a placeholder that never shipped or nothing at all, and the material arrives color only. A
set says which shipped textures are interchangeable, which one a bare material falls back
to, and which materials the set applies to. Bindings key on the FBX's own material name,
because a material whose bitmap path was emptied before export has no texture stem to key on.

**Companion maps** are the emissive and normal textures a pack ships for an atlas that no
FBX references, because that wiring lived in Unity materials the source drop omits. These
key on the atlas, since one emissive serves every material wearing it.

Paths here are pack-relative POSIX strings, because they cross into the Blender worker
through a JSON job file where an absolute path would be meaningless.

Pure Python: this module is imported both by the Blender worker and by the CLI.
"""

from __future__ import annotations

import fnmatch
import os


def relative(path, source_root):
    """A pack-relative POSIX path, which is the only shape that survives the job file."""
    return os.path.relpath(path, source_root).replace(os.sep, "/")


def matches_suffix(candidate, pattern):
    """True when a pack-relative path ends with a glob written as a path suffix.

    Suffix matching is the convention texture_overrides.json and foliage_overrides.json
    already use. Lowercasing both sides and using fnmatchcase keeps the result the same on
    Windows and Linux, which plain fnmatch does not.
    """
    return fnmatch.fnmatchcase(candidate.lower(), "*" + pattern.lower())


def expand_sets(config, textures, source_root, warnings):
    """Resolve every set's member globs against the textures a pack actually ships.

    A set whose default is not among its own members is dropped rather than guessed at: the
    default is applied without anybody asking for it by name, so a typo there would put an
    unintended texture on every model the set binds.

    ``cutout`` says a set's members are alpha cards rather than opaque surfaces. Such a card
    has no coverage of its own until the same image is bound as both color and mask, so the
    flag is carried through for the caller that fills a bare material.
    """
    relatives = sorted(relative(path, source_root) for path in textures)
    sets = {}
    for name, entry in sorted((config.get("flavors") or {}).items()):
        if name.startswith("_"):
            continue
        members = []
        for pattern in entry.get("members") or []:
            found = [rel for rel in relatives if matches_suffix(rel, pattern)]
            if not found:
                warnings.append(f"flavor set '{name}' member glob matched nothing: {pattern}")
            members.extend(found)
        members = sorted(set(members))
        if not members:
            continue
        target = entry.get("default") or ""
        chosen = [rel for rel in members if rel == target or rel.endswith("/" + target)]
        if len(chosen) != 1:
            warnings.append(f"flavor set '{name}' default '{target}' matches "
                            f"{len(chosen)} of its {len(members)} members; set ignored")
            continue
        sets[name] = {"members": members, "default": chosen[0],
                      "cutout": bool(entry.get("cutout"))}

    # One texture in two sets makes variant expansion ambiguous, since a material wearing it
    # would have two different families of sibling to generate.
    seen = {}
    for name, entry in sets.items():
        for member in entry["members"]:
            if member in seen:
                warnings.append(f"flavor sets '{seen[member]}' and '{name}' overlap on "
                                f"{member}")
            seen[member] = name
    return sets


# The channels a companion may declare. Albedo is what a companion hangs off rather than
# something it can supply, and alpha rides on the albedo's own file in glTF.
COMPANION_CHANNELS = ("emission", "normal")


def expand_companions(config, textures, source_root, warnings):
    """Resolve every declared companion map against the textures a pack actually ships.

    The two sides are deliberately asymmetric. A key may match many albedos, since one
    emissive commonly serves a whole set of recolors. A value must match exactly one
    texture, since a channel binds one file and there is no defensible way to choose among
    several; an ambiguous value is dropped rather than guessed at.

    Keys are tried in written order and the first to claim an albedo on a channel keeps it,
    so an overlap is reported rather than resolved by whichever pattern sorted first.
    """
    relatives = sorted(relative(path, source_root) for path in textures)
    companions = {}
    claimed = {}
    for pattern, channels in (config.get("companions") or {}).items():
        if pattern.startswith("_"):
            continue
        albedos = [rel for rel in relatives if matches_suffix(rel, pattern)]
        if not albedos:
            warnings.append(f"companion key matched nothing: {pattern}")
            continue
        found = {}
        for channel, target in sorted(channels.items()):
            if channel not in COMPANION_CHANNELS:
                warnings.append(f"companion for '{pattern}' names unknown channel "
                                f"'{channel}'; ignored")
                continue
            matched = [rel for rel in relatives if matches_suffix(rel, target)]
            if len(matched) != 1:
                warnings.append(f"companion {channel} '{target}' for '{pattern}' matches "
                                f"{len(matched)} textures; ignored")
                continue
            found[channel] = matched[0]
        for albedo in albedos:
            for channel, target in found.items():
                # A map is never its own atlas. Without this a loose key would bind an
                # emissive to itself and name a material after it.
                if albedo == target:
                    continue
                if (albedo, channel) in claimed:
                    warnings.append(f"companion keys '{claimed[(albedo, channel)]}' and "
                                    f"'{pattern}' overlap on {albedo} {channel}")
                    continue
                claimed[(albedo, channel)] = pattern
                companions.setdefault(albedo, {})[channel] = target
    return companions


def normalize_bindings(config, sets, warnings):
    """Fill in the optional keys and drop any binding naming a set that does not exist.

    Order is preserved and meaningful: the first binding whose globs both match wins, so a
    narrow model-scoped rule has to be written above the broad one it carves out of.
    """
    bindings = []
    for entry in config.get("bind") or []:
        flavor = entry.get("flavor")
        if flavor not in sets:
            warnings.append(f"binding for material '{entry.get('material') or '*'}' names "
                            f"unknown flavor set '{flavor}'; ignored")
            continue
        bindings.append({"model": entry.get("model") or "*",
                         "material": entry.get("material") or "*",
                         "flavor": flavor})
    return bindings


def match_binding(bindings, model_stem, material_name):
    """The first binding covering this material on this model, or None.

    Returns the whole binding rather than the flavor name, so the caller can report which
    rule fired and tell a stale table from a working one. Patterns anchor at the start
    here, unlike matches_suffix: a material name is a whole name, not the tail of a path.
    """
    for binding in bindings:
        if (fnmatch.fnmatchcase(model_stem.lower(), binding["model"].lower())
                and fnmatch.fnmatchcase(material_name.lower(), binding["material"].lower())):
            return binding
    return None


def flavor_fills(resolved):
    """Every flavor fill one model took, read before canonical names are deduplicated.

    ``distinct_materials`` collapses two materials that settled on the same canonical name,
    and if one was filled while the other resolved on its own, the fill marker can go with
    the loser. Reading fills off the pre-dedup records keeps them visible.
    """
    fills = {}
    for entry in resolved.values():
        albedo = (entry.get("channels") or {}).get("albedo") or {}
        if albedo.get("method") != "flavor":
            continue
        # One model filling the same binding twice is still one model in the report.
        fills[(albedo["binding_model"], albedo["binding"], entry["name"],
               albedo["flavor"])] = None
    return [{"binding_model": model, "binding": binding, "name": name, "flavor": flavor}
            for model, binding, name, flavor in fills]


def variants_of(member, sets):
    """The rest of the set this texture belongs to, or nothing when it belongs to none.

    Sets are checked in name order and the first hit wins, which only matters when two
    overlap, and expand_sets has already warned about that. Member order is preserved as
    expand_sets produced it.
    """
    for name in sorted(sets):
        members = sets[name]["members"]
        if member in members:
            return [other for other in members if other != member]
    return []
