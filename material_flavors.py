"""Interchangeable texture sets, and the materials that default into them.

Synty ships several textures for a surface whose choice belongs to the consumer: five
tileable walls, eight roofs, three recolours of one atlas. The FBX names a placeholder that
never shipped, or names nothing at all, so ``texture_matching`` has nothing to decode and
the material arrives colour only.

This module holds the curated answer instead: which shipped textures form a set, which
member a bare material falls back to, and which materials each set applies to. Bindings are
keyed on the FBX's own material name, because a material whose bitmap path was emptied
before export has no texture stem to key on. Listing a set's other members is that same
knowledge read the other way, and is what lets a converted model arrive with every
alternative already available to swap to.

Paths here are pack-relative POSIX strings. They cross into the Blender worker through a
JSON job file, where an absolute path would be meaningless: the worker joins them onto the
source root to read a texture and onto the output root to point a material at one.

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
    default is the one member that gets applied without anybody asking for it, so a typo
    there would put an unintended texture on every model the set binds.

    A set may also declare ``cutout``, which says its default is an alpha card rather than
    an opaque surface: a Synty foliage or netting quad has no coverage of its own to cut
    with until the same image is bound as both colour and mask. The flag is carried through
    unchanged, defaulting to false when a set does not declare it, so a caller filling a bare
    material can tell the two cases apart.
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

    Returns the whole binding rather than the flavor name so the caller can report which
    rule fired, which is what tells a stale table from a working one. Patterns anchor at
    the start here, unlike matches_suffix, because a material name and a filename stem are
    whole names rather than the tail of a path.
    """
    for binding in bindings:
        if (fnmatch.fnmatchcase(model_stem.lower(), binding["model"].lower())
                and fnmatch.fnmatchcase(material_name.lower(), binding["material"].lower())):
            return binding
    return None


def variants_of(member, sets):
    """The rest of the set this texture belongs to, or nothing when it belongs to none.

    Sets are checked in name order and the first hit wins, which only matters when two of
    them overlap, and expand_sets has already warned about that. Members arrive already
    sorted from expand_sets, and this preserves that order rather than sorting again, so a
    hand-built sets dict would come back in whatever order it was written.
    """
    for name in sorted(sets):
        members = sets[name]["members"]
        if member in members:
            return [other for other in members if other != member]
    return []
