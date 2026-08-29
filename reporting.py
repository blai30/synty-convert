"""Everything the converter prints.

The material report is not just a log. It is the tool the curated override files are
authored from, so it names what resolved by heuristic, what a flavor set filled, which
companion maps reached a material, and which shipped maps nothing has been told to use.
A table that has silently stopped matching is the main way those files rot.
"""

from __future__ import annotations

import collections
import re
import statistics
from pathlib import Path

import material_flavors
from manifests import channel_of

# Compared against a pack's median model, never an individual one. Synty ships plenty of
# coins and gems under 10 cm, but a pack whose typical model is that small has had a unit
# it is not in folded into every file.
MIN_PLAUSIBLE_SPAN = 0.1

# Channels worth reviewing a reference for. All four resolve through the same matcher.
REPORTED_CHANNELS = ("albedo", "emission", "normal", "alpha")

# Matches "emissive" and "emission" along with the spellings Synty has actually shipped:
# Emmisive, Emmissive, Emmision. Tolerating one or two of the m and the s covers every one
# without matching unrelated words.
EMISSIVE_NAME_PATTERN = re.compile(r"em{1,2}is{1,2}")


def human(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024.0 or unit == "GB":
            return f"{size:,.1f} {unit}"
        size /= 1024.0


def companion_named(path):
    """True when a texture's name reads as an emissive or normal map rather than an albedo.

    Name only, and deliberately loose. This decides what goes on the authoring worklist,
    not what gets bound, so a false positive costs a glance while a false negative means a
    map nobody ever notices is missing.
    """
    stem = Path(path).stem.lower()
    return (EMISSIVE_NAME_PATTERN.search(stem) is not None
            or "normal" in stem
            # PolygonGangWarfare_Leaves_Nrml.png ships the abbreviation with no vowels.
            or "nrml" in stem
            or stem.endswith("_bump"))


def reviewable_channels(materials):
    """Every (material, channel, resolution) worth reporting a reference for.

    A mask naming the same file the material is colored with resolved with it, so listing
    it again would say the same thing twice.
    """
    found = []
    for entry in materials.values():
        albedo = channel_of(entry, "albedo")
        for name in REPORTED_CHANNELS:
            channel = channel_of(entry, name)
            if not channel.get("reference"):
                continue
            if name == "alpha" and channel["reference"] == albedo.get("reference"):
                continue
            found.append((entry, name, channel))
    return found


def fills_for(totals, pack):
    """This pack's flavor fills, keyed for reporting, and the bindings that produced them.

    Read from totals.filled rather than from the merged materials: a filled material
    renames itself after its new texture and can merge with an identically named record
    that resolved normally, at which point the fill disappears from the merge.
    """
    filled = collections.Counter()
    fired = set()
    for (fill_pack, model, binding, name, flavor), count in totals.filled.items():
        if fill_pack == pack:
            filled[(binding, name, flavor)] += count
            # A binding is identified by both of its globs: a narrow model-scoped rule can
            # sit above a broader one sharing the same material glob.
            fired.add((model, binding))
    return filled, fired


def summary_line(pack, materials, filled, asked):
    counts = collections.Counter(found.get("method") or "unresolved" for _, _, found in asked)
    # A flavor fill leaves `reference` empty but does carry a texture, so it must be
    # excluded here too or a material the pack just fixed still reads as untextured.
    untextured = sum(1 for entry in materials.values()
                     if not channel_of(entry, "albedo").get("reference")
                     and not channel_of(entry, "albedo").get("texture"))
    print(f"  {pack}: {len(materials)} materials  "
          f"({counts['exact'] + counts['normalized']} exact, {counts['override']} override, "
          f"{counts['tokens'] + counts['trimmed']} heuristic, {counts['unresolved']} unresolved, "
          f"{len(filled)} filled, {untextured} untextured)")
    extra = collections.Counter(name for entry in materials.values()
                                for name in material_flavors.COMPANION_CHANNELS
                                if channel_of(entry, name).get("texture"))
    if extra:
        print("     " + ", ".join(f"{count} carry an {name} map" if name == "emission"
                                  else f"{count} carry a {name} map"
                                  for name, count in sorted(extra.items())))


def report_flavors(sets, filled):
    for name in sorted(sets):
        print(f"     flavor   {name}  -> {len(sets[name]['members'])} textures, "
              f"default {Path(sets[name]['default']).name}")
    for (binding, name, flavor), count in sorted(filled.items(), key=lambda item: -item[1]):
        print(f"     filled   {binding:<24} -> {name}  ({count} files, flavor {flavor})")


def companions_worn(materials):
    """How many models each companion binding actually reached.

    Read off the observed channels rather than off the declaration, so an entry that
    resolves against the texture index but that nothing in the pack wears is still dead.
    """
    worn = collections.Counter()
    for material in materials.values():
        albedo = channel_of(material, "albedo").get("member")
        for channel in material_flavors.COMPANION_CHANNELS:
            if channel_of(material, channel).get("method") == "companion":
                worn[(albedo, channel)] += material["used_by"]
    return worn


def companions_reachable(materials, sets, companions, worn):
    """The bindings that do real work, which is more than the ones a model wears.

    write_manifests generates a sibling record for every other member of a set some
    observed material drew from, and each sibling takes its own member's companions. A
    binding on such a member is therefore reachable even though nothing wears it here.
    """
    observed = {channel_of(material, "albedo").get("member") for material in materials.values()}
    reachable = set(worn)
    for definition in sets.values():
        if not observed & set(definition["members"]):
            continue
        for member in definition["members"]:
            for channel in material_flavors.COMPANION_CHANNELS:
                if channel in (companions.get(member) or {}):
                    reachable.add((member, channel))
    return reachable


def report_companions(materials, sets, companions, judge):
    """One line per declared companion saying what became of it."""
    worn = companions_worn(materials)
    reachable = companions_reachable(materials, sets, companions, worn)
    for (albedo, channel), count in sorted(worn.items(), key=lambda item: -item[1]):
        target = (companions.get(albedo) or {}).get(channel, "?")
        print(f"     companion {Path(albedo).name:<32} -> {channel} "
              f"{Path(target).name}  ({count} models)")
    for albedo, channel in sorted(reachable - set(worn)):
        target = (companions.get(albedo) or {}).get(channel, "?")
        print(f"     sibling   {Path(albedo).name:<32} -> {channel} "
              f"{Path(target).name}  (0 models wear it; a generated sibling will)")
    if not judge:
        return
    for albedo, declared in sorted(companions.items()):
        for channel in sorted(declared):
            if (albedo, channel) not in reachable:
                print(f"     DEAD     companion '{albedo}' {channel} reached no material; "
                      f"remove it from material_overrides.json or fix its glob")


def report_candidates(materials, companions, context):
    """The authoring worklist: maps the pack ships that nothing has been told to use.

    A candidate is not a defect, since several are genuinely unbindable, but every one
    should end up either bound or knowingly left, never merely unnoticed.
    """
    source_root = context.get("source_root") or ""
    bound = {found.get("member") for material in materials.values()
             for name in REPORTED_CHANNELS
             for found in [channel_of(material, name)] if found.get("member")}
    claimed = {target for declared in companions.values() for target in declared.values()}
    candidates = sorted(name for name in (material_flavors.relative(path, source_root)
                                          for path in context.get("textures") or [])
                        if companion_named(name) and name not in bound and name not in claimed)
    if not candidates:
        return
    shown = ", ".join(Path(name).name for name in candidates[:6])
    more = f" ... and {len(candidates) - 6} more" if len(candidates) > 6 else ""
    print(f"     candidates {len(candidates)} unbound companion map(s): {shown}{more}")


def report_bindings(bindings, companions, fired, judge):
    """Name any binding that never fired, or say why this run cannot tell."""
    if judge:
        for binding in bindings:
            if (binding["model"], binding["material"]) not in fired:
                print(f"     DEAD     binding '{binding['material']}' on model "
                      f"'{binding['model']}' matched nothing; remove it from "
                      f"material_overrides.json or fix its glob")
    elif bindings or companions:
        kinds = [kind for kind, table in (("binding", bindings), ("companion", companions))
                 if table]
        print(f"     {' and '.join(kinds)} health not assessed this run; rerun with "
              f"--untextured fill --force to judge it")


def report_references(asked):
    """Every reference a human should look at: heuristic matches, then failures."""
    by_usage = sorted(asked, key=lambda item: -item[0]["used_by"])
    for entry, channel, found in by_usage:
        # A trimmed match dropped tokens to find its winner, so it is a guess like any
        # other heuristic and belongs in front of the reader.
        if found.get("method") in ("tokens", "trimmed", "override"):
            label = "manual " if found["method"] == "override" else "review "
            suffix = "" if channel == "albedo" else f" [{channel}]"
            print(f"     {label} {found['reference']} -> {entry['name']}{suffix} "
                  f"({entry['used_by']} files)")
    for entry, channel, found in by_usage:
        if not found.get("method"):
            lost = "color only" if channel == "albedo" else f"no {channel} map"
            print(f"     UNRESOLVED  {found['reference']}  ({entry['used_by']} files, "
                  f"{lost}; add to texture_overrides.json)")


def report_materials(totals, contexts, judge_bindings=True):
    """Print how every texture reference resolved, so heuristic matches can be reviewed.

    A DEAD verdict is an absence claim, and only a run that could have watched a binding
    fire is entitled to make one: `keep` and `drop` never hand the worker a binding table,
    and an incremental run only examined the models it reconverted. ``judge_bindings`` says
    which kind of run this was.
    """
    if not any(totals.materials.values()):
        return
    print("\nMaterials")
    for pack, materials in sorted(totals.materials.items()):
        if not materials:
            continue
        context = contexts.get(pack) or {}
        declared = context.get("materials") or {}
        sets = declared.get("sets") or {}
        companions = declared.get("companions") or {}
        asked = reviewable_channels(materials)
        filled, fired = fills_for(totals, pack)

        summary_line(pack, materials, filled, asked)
        report_flavors(sets, filled)
        report_companions(materials, sets, companions, judge_bindings)
        report_candidates(materials, companions, context)
        report_bindings(declared.get("bind") or [], companions, fired, judge_bindings)
        report_references(asked)


def report_scale(totals):
    """Flag packs whose models come out too small to be real.

    A pack that declares the wrong unit converts a hundred times under size, and nothing
    else notices: the file is valid, the axes are right and the transforms are identity.
    """
    for pack, spans in sorted(totals.spans.items()):
        median = statistics.median(spans)
        if median >= MIN_PLAUSIBLE_SPAN:
            continue
        print(f"\n  {pack}: the median model is {median:.4f} m across, which means this "
              f"pack's FBX\n  declare a unit their geometry is not in. Add a scale for it to "
              f"scale_overrides.json\n  and reconvert with --force.")


def failure_reason(src, message):
    """A worker's error with the model's own path taken back out of it.

    Blender quotes the absolute filename inside several of its import errors. The report
    line already names the file, and leaving the path in makes one fault hitting twenty
    models read as twenty distinct faults.
    """
    forms = {str(src), str(src).replace("\\", "/"), str(src).replace("/", "\\")}
    text = " ".join(message)
    for form in forms | {form.replace("\\", "\\\\") for form in forms}:
        text = text.replace(form, "")
    return " ".join(text.split()).strip(" '\"") or "unknown error"


def report_failures(totals):
    """Group every model that did not convert under the reason it did not.

    Nine packs ship a file called SM_Prop_Barrel_01.fbx, so a basename alone does not
    identify one. Grouping also keeps a fault hitting one model visible beside one hitting
    twenty, which a flat truncated list does not.
    """
    if not totals.failures:
        return
    grouped = collections.defaultdict(list)
    for src, pack, message in totals.failures:
        name = Path(str(src)).name
        grouped[failure_reason(src, message)].append(f"{pack}/{name}" if pack else name)
    print(f"\n{len(totals.failures)} failure(s):")
    for reason, models in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        print(f"  {reason}: {len(models)}")
        for model in sorted(models)[:5]:
            print(f"     {model}")
        if len(models) > 5:
            print(f"     ... and {len(models) - 5} more")


def report(totals, elapsed):
    print("\n" + "=" * 68)
    print(f"Converted {totals.converted} FBX -> GLB   "
          f"(failed {totals.failed}, up to date {totals.skipped})")
    print(f"Copied    {totals.copied} other files ({human(totals.copied_bytes)})")
    if totals.split:
        records = [record for entries in totals.split.values() for record in entries]
        # An uncapped neck is only visible once a head is hidden in game, far from here,
        # so it gets its own line rather than a warning the list below may truncate.
        open_necks = sum(1 for record in records if record.get("open_rings"))
        summary = f"Split     {len(records)} head(s) off {len(totals.split)} character model(s)"
        print(summary + (f", {open_necks} left open at the neck" if open_necks else ""))
    if totals.lods:
        print(f"LODs      dropped {sum(totals.lods.values())} coarse level(s) from "
              f"{len(totals.lods)} model(s)")
    if totals.foliage:
        print(f"Foliage   bound {sum(totals.foliage.values())} mesh(es) across "
              f"{len(totals.foliage)} model(s) whose FBX named no texture")
    if totals.repaired:
        print(f"Repaired  {totals.repaired} ASCII FBX transcoded to binary before import")
    if totals.untextured:
        print(f"Untextured {len(totals.untextured)} model(s) not written, "
              f"no material bound a texture")
        for pack, count in sorted(collections.Counter(totals.untextured.values()).items()):
            print(f"          {count:6d}  {pack}")
    if totals.converted:
        saved = totals.src_bytes - totals.dst_bytes
        ratio = 100.0 * saved / max(totals.src_bytes, 1)
        print(f"Model size {human(totals.src_bytes)} -> {human(totals.dst_bytes)}"
              f"   saved {human(saved)} ({ratio:.1f}% smaller)")
    print(f"Elapsed   {elapsed:.1f}s")

    if totals.grew:
        print(f"\n{len(totals.grew)} file(s) did not shrink:")
        for src, before, after in totals.grew[:10]:
            print(f"  {Path(src).name}: {human(before)} -> {human(after)}")
    if totals.warnings:
        print(f"\n{len(totals.warnings)} warning(s):")
        for warning in totals.warnings[:15]:
            print(f"  {warning}")
    report_failures(totals)
    print("=" * 68)
