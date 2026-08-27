"""Convert Synty asset packs from FBX to Godot-ready GLB.

Mirrors the source tree into the output directory, replacing every ``.fbx`` with a
``.glb`` and copying all other files (textures, licenses) untouched. Materials reference
the pack's shared texture atlas rather than embedding a copy of it.

    python synty_convert.py --packs POLYGON_BattleRoyale ANIMATION_Base

Conversion itself runs inside Blender; see ``blender_convert.py``. Work is spread over a
pool of Blender processes, each handling many files per launch so the startup cost is
paid once per worker rather than once per file.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

RESULT_PREFIX = "@@RESULT "
WORKER_SCRIPT = Path(__file__).with_name("blender_convert.py")
OVERRIDES_FILE = Path(__file__).with_name("texture_overrides.json")
SCALES_FILE = Path(__file__).with_name("scale_overrides.json")
FOLIAGE_FILE = Path(__file__).with_name("foliage_overrides.json")
MATERIALS_FILE = Path(__file__).with_name("material_overrides.json")

# Compared against a pack's median model, never an individual one. Synty ships plenty of
# coins and gems under 10 cm, but a pack whose typical model is that small has had a unit
# it is not in folded into every file.
MIN_PLAUSIBLE_SPAN = 0.1

sys.path.insert(0, str(Path(__file__).resolve().parent))
import material_flavors
import texture_matching

BLENDER_CANDIDATES = [
    "blender",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
    "/Applications/Blender.app/Contents/MacOS/Blender",
]


@dataclass
class Job:
    src: Path
    dst: Path
    pack: str = ""
    split: bool = False
    scale: float = 1.0


@dataclass
class Totals:
    converted: int = 0
    failed: int = 0
    skipped: int = 0
    copied: int = 0
    src_bytes: int = 0
    dst_bytes: int = 0
    copied_bytes: int = 0
    grew: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    materials: dict = field(default_factory=dict)
    split: dict = field(default_factory=dict)
    lods: dict = field(default_factory=dict)
    foliage: dict = field(default_factory=dict)
    untextured: dict = field(default_factory=dict)
    # Raw per-model scan records, kept only when --scan-report asks for them.
    scanned: list = field(default_factory=list)
    spans: dict = field(default_factory=lambda: collections.defaultdict(list))


def find_blender(explicit):
    for candidate in filter(None, [explicit, os.environ.get("BLENDER")] + BLENDER_CANDIDATES):
        resolved = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if resolved:
            return resolved
    sys.exit("Blender not found. Pass --blender <path> or set the BLENDER environment variable.")


def human(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024.0 or unit == "GB":
            return f"{size:,.1f} {unit}"
        size /= 1024.0


def pack_of(path, source_root):
    """The top-level pack directory a file belongs to."""
    relative = path.relative_to(source_root)
    return relative.parts[0] if len(relative.parts) > 1 else "."


def discover(source_root, output_root, patterns):
    """Split the source tree into FBX conversion jobs and verbatim copies."""
    jobs, copies = [], []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if patterns and not any(fnmatch.fnmatch(pack_of(path, source_root), f"*{p}*") for p in patterns):
            continue
        relative = path.relative_to(source_root)
        if path.suffix.lower() == ".fbx":
            jobs.append(Job(path, output_root / relative.with_suffix(".glb"), pack_of(path, source_root)))
        else:
            copies.append(Job(path, output_root / relative, pack_of(path, source_root)))
    return jobs, copies


def mark_splits(jobs, patterns):
    """Flag the jobs whose characters should have their heads split onto their own node.

    An empty pattern list means every job is offered up; whether a file turns out to hold
    a character is decided in Blender by looking for a rig with a head bone, since a name
    cannot tell you that. Patterns narrow the offer to matching filenames.
    """
    for job in jobs:
        job.split = not patterns or any(fnmatch.fnmatch(job.src.name, f"*{p}*") for p in patterns)


def mark_scales(jobs):
    """Attach the unit-scale correction each job's pack needs, if it needs one.

    A Synty FBX declares the unit its geometry is in and the importer converts from it, so
    normally there is nothing to correct. Some packs declare the wrong one; see
    ``scale_overrides.json`` for which, and why.
    """
    if not SCALES_FILE.exists():
        return
    overrides = json.loads(SCALES_FILE.read_text(encoding="utf-8"))
    for job in jobs:
        entry = overrides.get(job.pack)
        if not entry:
            continue
        # A pack-wide scale with per-file exceptions, since the packs that get one wrong
        # tend to get it wrong for most files rather than all of them.
        job.scale = next((value for pattern, value in entry.get("files", {}).items()
                          if fnmatch.fnmatch(job.src.stem, pattern)), entry.get("scale", 1.0))


def pack_contexts(packs, source_root, output_root):
    """Per-pack texture index and manual overrides, shared with every worker.

    Returns the contexts and any complaint the override files made about themselves, which
    the caller prints: a curated table that has silently stopped matching is the main way
    these files rot.
    """
    overrides = {}
    if OVERRIDES_FILE.exists():
        overrides = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    foliage = {}
    if FOLIAGE_FILE.exists():
        foliage = json.loads(FOLIAGE_FILE.read_text(encoding="utf-8"))
    flavors = {}
    if MATERIALS_FILE.exists():
        flavors = json.loads(MATERIALS_FILE.read_text(encoding="utf-8"))
    indexes = {}
    warnings = []

    def index(pack):
        if pack not in indexes:
            indexes[pack] = texture_matching.index_textures(source_root / pack)
        return indexes[pack]

    contexts = {}
    for pack in packs:
        entries = {k: v for k, v in overrides.get(pack, {}).items() if not k.startswith("_")}
        # An override may point into another pack, which then has to be indexed as well,
        # whether or not that pack is itself part of this run.
        foreign = {}
        for target in entries.values():
            if texture_matching.FOREIGN_SEPARATOR in target:
                other = target.split(texture_matching.FOREIGN_SEPARATOR, 1)[0]
                foreign[other] = index(other)
        config = {k: v for k, v in flavors.get(pack, {}).items() if not k.startswith("_")}
        complaints = []
        sets = material_flavors.expand_sets(config, index(pack), str(source_root / pack),
                                            complaints)
        bindings = material_flavors.normalize_bindings(config, sets, complaints)
        warnings.extend(f"{pack}: {complaint}" for complaint in complaints)
        contexts[pack] = {
            "source_root": str(source_root / pack),
            "output_root": str(output_root / pack),
            "textures": index(pack),
            "overrides": entries,
            "foreign": foreign,
            "foliage": {k: v for k, v in foliage.get(pack, {}).items() if not k.startswith("_")},
            "materials": {"sets": sets, "bind": bindings},
        }
    return contexts, warnings


def is_current(job):
    """True when the output already exists and is newer than its source."""
    return job.dst.exists() and job.dst.stat().st_mtime >= job.src.stat().st_mtime


def copy_assets(copies, force, totals):
    for job in copies:
        if not force and is_current(job):
            continue
        job.dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(job.src, job.dst)
        totals.copied += 1
        totals.copied_bytes += job.src.stat().st_size


def run_worker(blender, jobs, options, contexts, on_result):
    """Run one Blender process over a batch of jobs, streaming results as they arrive."""
    handle, job_path = tempfile.mkstemp(suffix=".json", prefix="synty_")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"options": options, "packs": contexts,
                   "jobs": [{"src": str(j.src), "dst": str(j.dst), "pack": j.pack,
                             "split": j.split, "scale": j.scale} for j in jobs]}, stream)

    command = [blender, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(WORKER_SCRIPT), "--", job_path]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1)
        for line in process.stdout:
            if line.startswith(RESULT_PREFIX):
                on_result(json.loads(line[len(RESULT_PREFIX):]))
        process.wait()
        if process.returncode != 0:
            on_result({"ok": False, "src": f"<worker batch of {len(jobs)}>", "dst": "",
                       "error": f"Blender exited with code {process.returncode}"})
    finally:
        os.unlink(job_path)


def chunk(jobs, count):
    """Deal jobs round-robin so every worker gets a mix of large and small files."""
    buckets = [jobs[i::count] for i in range(count)]
    return [bucket for bucket in buckets if bucket]


def convert_all(blender, jobs, options, contexts, workers, totals, quiet):
    done = 0
    total = len(jobs)

    def tick():
        if not quiet and (done % 25 == 0 or done == total):
            ratio = 100.0 * (1 - totals.dst_bytes / max(totals.src_bytes, 1))
            print(f"  [{done}/{total}] {human(totals.src_bytes)} -> {human(totals.dst_bytes)} ({ratio:.1f}% smaller)")

    def record(result):
        nonlocal done
        done += 1
        if not result.get("ok"):
            totals.failed += 1
            totals.failures.append((result.get("src"), result.get("error", "").strip().splitlines()[-1:]))
            print(f"  [{done}/{total}] FAILED {Path(result.get('src', '?')).name}")
            return
        if options.get("scan_report"):
            totals.scanned.append({"src": result.get("src"), "pack": result.get("pack", ""),
                                   "materials": result.get("materials", [])})
        if result.get("untextured"):
            # Nothing was written and no material survives to describe, so this contributes
            # neither bytes to the totals nor an entry to the pack's manifest. Its warnings
            # are the unresolved references that got it dropped, which the summary line
            # already says, so they stay out of a list meant for what shipped.
            totals.untextured[result["src"]] = result.get("pack", "")
            tick()
            return
        totals.converted += 1
        totals.src_bytes += result["src_bytes"]
        totals.dst_bytes += result["dst_bytes"]
        pack = totals.materials.setdefault(result.get("pack", ""), {})
        for material in result.get("materials", []):
            entry = pack.setdefault(material["name"], dict(material, used_by=0, sources=set()))
            entry["used_by"] += 1
            entry["sources"].add(material["source"])
        if result.get("split"):
            totals.split[result["src"]] = result["split"]
        if result.get("dropped_lods"):
            totals.lods[result["src"]] = result["dropped_lods"]
        if result.get("foliage"):
            totals.foliage[result["src"]] = result["foliage"]
        bounds = (result.get("summary") or {}).get("bounds")
        if bounds:
            totals.spans[result.get("pack", "")].append(max(bounds[i + 3] - bounds[i] for i in range(3)))
        if result["dst_bytes"] >= result["src_bytes"]:
            totals.grew.append((result["src"], result["src_bytes"], result["dst_bytes"]))
        for warning in result.get("warnings", []):
            totals.warnings.append(f"{Path(result['src']).name}: {warning}")
        check = result.get("verify")
        if check and not check.get("ok"):
            totals.warnings.append(f"{Path(result['src']).name}: verification mismatch {check}")
        tick()

    batches = chunk(jobs, workers)
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        for batch in batches:
            pool.submit(run_worker, blender, batch, options, contexts, record)


def linear_to_srgb(value):
    """Convert a linear colour channel to the sRGB space Godot's albedo_color expects.

    Blender and glTF both store base colour linearly; StandardMaterial3D.albedo_color is
    sRGB. Writing the linear number straight into a .tres renders noticeably too dark.
    """
    if value <= 0.0031308:
        return round(value * 12.92, 6)
    return round(1.055 * (value ** (1 / 2.4)) - 0.055, 6)


def as_res_path(path, output_root, res_prefix):
    """Where a converted file will live in the Godot project it gets copied into.

    This repo is a converter, not a Godot project, so paths are expressed relative to the
    output root and prefixed with wherever the user intends to drop it.
    """
    relative = Path(path).resolve().relative_to(output_root)
    return f"{res_prefix.rstrip('/')}/" + str(relative).replace(os.sep, "/")


def channel_of(entry, name):
    """One resolved texture channel of a material record, or an empty one."""
    return (entry.get("channels") or {}).get(name) or {}


def write_manifests(totals, materials_root, output_root, res_prefix):
    """Write one manifest per pack for the Godot material generator to consume.

    The converter deliberately stops here rather than authoring .tres itself, so that
    Godot assigns every resource id and uid. Every channel the GLB carries is described
    here too, so a shared material and the model's own render the same way.
    """
    written = []
    for pack, materials in sorted(totals.materials.items()):
        if not pack or not materials:
            continue
        entries = []
        for name, entry in sorted(materials.items()):
            albedo, emission = channel_of(entry, "albedo"), channel_of(entry, "emission")
            normal = channel_of(entry, "normal")
            record = {"name": name, "used_by": entry["used_by"],
                      "source_names": sorted(entry["sources"])}
            if albedo.get("texture"):
                record["albedo_texture"] = as_res_path(albedo["texture"], output_root, res_prefix)
            else:
                record["albedo_color"] = [*(linear_to_srgb(c) for c in entry["color"]), entry["alpha"]]
            if emission.get("texture"):
                # The map stands in for the emissive colour, the way Maya treats a connected
                # file, so the colour is not written alongside it.
                record["emission_texture"] = as_res_path(emission["texture"], output_root, res_prefix)
                record["emission_energy"] = entry["emission_strength"]
            elif any(entry["emission_color"]):
                record["emission_color"] = [linear_to_srgb(c) for c in entry["emission_color"]]
                record["emission_energy"] = entry["emission_strength"]
            if normal.get("texture"):
                record["normal_texture"] = as_res_path(normal["texture"], output_root, res_prefix)
                record["normal_scale"] = entry["normal_strength"]
            record["roughness"] = entry["roughness"]
            record["metallic"] = entry["metallic"]
            if entry.get("transparency"):
                record["transparency"] = entry["transparency"]
            for channel in ("albedo", "emission", "normal", "alpha"):
                asked = channel_of(entry, channel)
                # A mask naming the file the material is coloured with says nothing new.
                if not asked.get("reference") or (channel == "alpha" and asked.get("reference")
                                                  == albedo.get("reference")):
                    continue
                key = "reference" if channel == "albedo" else channel + "_reference"
                record[key] = asked["reference"]
                record["match" if channel == "albedo" else channel + "_match"] = asked.get("method")
            entries.append(record)
        target = materials_root / pack / "materials.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"pack": pack, "materials": entries}, indent=2) + "\n",
                          encoding="utf-8")
        written.append((target, len(entries)))
    return written


def report_materials(totals):
    """Print how every texture reference resolved, so heuristic matches can be reviewed."""
    if not any(totals.materials.values()):
        return
    print("\nMaterials")
    for pack, materials in sorted(totals.materials.items()):
        if not materials:
            continue
        # Every channel resolves through the same matcher, so all of them are reviewable.
        # A mask that names the same file the material is coloured with resolved with it, so
        # listing it again would say the same thing twice.
        asked = [(entry, name, channel_of(entry, name)) for entry in materials.values()
                 for name in ("albedo", "emission", "normal", "alpha")
                 if channel_of(entry, name).get("reference")
                 and not (name == "alpha" and channel_of(entry, name).get("reference")
                          == channel_of(entry, "albedo").get("reference"))]
        counts = collections.Counter(found.get("method") or "unresolved" for _, _, found in asked)
        untextured = sum(1 for e in materials.values() if not channel_of(e, "albedo").get("reference"))
        extra = collections.Counter(name for e in materials.values() for name in
                                    ("emission", "normal") if channel_of(e, name).get("texture"))
        print(f"  {pack}: {len(materials)} materials  "
              f"({counts['exact'] + counts['normalized']} exact, {counts['override']} override, "
              f"{counts['tokens'] + counts['trimmed']} heuristic, {counts['unresolved']} unresolved, "
              f"{untextured} untextured)")
        if extra:
            print("     " + ", ".join(f"{count} carry an {name} map" if name == "emission"
                                      else f"{count} carry a {name} map"
                                      for name, count in sorted(extra.items())))
        for entry, channel, found in sorted(asked, key=lambda item: -item[0]["used_by"]):
            # A trimmed match dropped tokens to find its winner, so it is a guess like any
            # other heuristic and belongs in front of the reader, not folded into the exact count.
            if found.get("method") in ("tokens", "trimmed", "override"):
                label = "manual " if found["method"] == "override" else "review "
                suffix = "" if channel == "albedo" else f" [{channel}]"
                print(f"     {label} {found['reference']} -> {entry['name']}{suffix} "
                      f"({entry['used_by']} files)")
        for entry, channel, found in sorted(asked, key=lambda item: -item[0]["used_by"]):
            if not found.get("method"):
                lost = "colour only" if channel == "albedo" else f"no {channel} map"
                print(f"     UNRESOLVED  {found['reference']}  ({entry['used_by']} files, "
                      f"{lost}; add to texture_overrides.json)")


def report_scale(totals):
    """Flag packs whose models come out too small to be real.

    A pack that declares the wrong unit converts to geometry a hundred times under size,
    and nothing else here notices: the file is valid, the axes are right and the node
    transforms are identity. It only shows up once a model is dragged into a scene.
    """
    for pack, spans in sorted(totals.spans.items()):
        median = statistics.median(spans)
        if median >= MIN_PLAUSIBLE_SPAN:
            continue
        print(f"\n  {pack}: the median model is {median:.4f} m across, which means this "
              f"pack's FBX\n  declare a unit their geometry is not in. Add a scale for it to "
              f"{SCALES_FILE.name}\n  and reconvert with --force.")


def report(totals, elapsed):
    print("\n" + "=" * 68)
    print(f"Converted {totals.converted} FBX -> GLB   (failed {totals.failed}, up to date {totals.skipped})")
    print(f"Copied    {totals.copied} other files ({human(totals.copied_bytes)})")
    if totals.split:
        records = [record for entries in totals.split.values() for record in entries]
        # An uncapped neck is only visible once a head is hidden in game, far from here,
        # so it gets its own line rather than a warning that the list below may truncate.
        open_necks = sum(1 for record in records if record.get("open_rings"))
        summary = f"Split     {len(records)} head(s) off {len(totals.split)} character model(s)"
        print(summary + (f", {open_necks} left open at the neck" if open_necks else ""))
    if totals.lods:
        print(f"LODs      dropped {sum(totals.lods.values())} coarse level(s) from "
              f"{len(totals.lods)} model(s)")
    if totals.foliage:
        print(f"Foliage   bound {sum(totals.foliage.values())} mesh(es) across "
              f"{len(totals.foliage)} model(s) whose FBX named no texture")
    if totals.untextured:
        print(f"Untextured {len(totals.untextured)} model(s) not written, no material bound a texture")
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
    if totals.failures:
        print(f"\n{len(totals.failures)} failure(s):")
        for src, message in totals.failures[:15]:
            print(f"  {Path(str(src)).name}: {' '.join(message)}")
    print("=" * 68)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent
    parser.add_argument("--src", type=Path, default=root / "synty_packs_fbx", help="source pack directory")
    parser.add_argument("--dst", type=Path, default=root / "assets", help="output directory")
    parser.add_argument("--packs", nargs="*", default=None,
                        help="only convert packs whose folder name contains one of these substrings")
    parser.add_argument("-j", "--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2),
                        help="number of concurrent Blender processes")
    parser.add_argument("--force", action="store_true", help="reconvert files that are already up to date")
    parser.add_argument("--verify", action="store_true",
                        help="reimport each GLB and check vertex, bone and bounding-box parity")
    parser.add_argument("--vertex-colors", choices=("drop", "keep"), default="drop",
                        help="drop vertex colors for barebones meshes, or keep them")
    parser.add_argument("--animations", choices=("keep", "drop"), default="keep",
                        help="drop baked-in takes; useful for model packs whose clips live elsewhere")
    parser.add_argument("--lods", choices=("drop", "keep"), default="drop",
                        help="drop every LOD level above the finest, since Godot builds its own "
                             "chain; 'keep' ships all of them, which renders them all at once")
    parser.add_argument("--materials", choices=("external", "none"), default="external",
                        help="'external' references shared textures by URI and writes Godot manifests; "
                             "'none' strips materials for barebones meshes")
    parser.add_argument("--materials-dir", type=Path, default=root / "materials",
                        help="where per-pack material manifests are written")
    parser.add_argument("--res-prefix", default=None,
                        help="res:// location the converted assets will live at in your Godot "
                             "project (default: res://<output folder name>)")
    parser.add_argument("--split-heads", nargs="*", default=None, metavar="NAME",
                        help="split every rigged character's head onto its own mesh node, so it "
                             "can be hidden or moved to another render layer in Godot; "
                             "name substrings limit this to matching files")
    parser.add_argument("--skip-untextured", action="store_true",
                        help="do not write a model whose materials bound no texture at all, "
                             "since it ships as a flat white or grey blob; deletes any such "
                             "model an earlier run left behind. Animation files, which carry "
                             "no mesh, are unaffected")
    parser.add_argument("--scan-materials", action="store_true",
                        help="report how texture references resolve, without converting anything")
    parser.add_argument("--scan-report", type=Path, default=None, metavar="PATH",
                        help="with --scan-materials, also write every model's raw material "
                             "records to PATH as JSON; this is what the per-pack override "
                             "tables are authored from")
    parser.add_argument("--dry-run", action="store_true", help="list what would happen and exit")
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    parser.add_argument("--blender", default=None, help="path to the Blender executable")
    args = parser.parse_args()

    source_root = args.src.resolve()
    output_root = args.dst.resolve()
    if not source_root.is_dir():
        sys.exit(f"Source directory not found: {source_root}")
    if args.skip_untextured and args.materials == "none":
        # --materials none strips every material, so nothing would bind a texture and the
        # run would write nothing at all.
        sys.exit("--skip-untextured needs materials to judge; it cannot be used with --materials none.")
    if args.scan_report and not args.scan_materials:
        sys.exit("--scan-report only has anything to write during --scan-materials.")

    jobs, copies = discover(source_root, output_root, args.packs)
    if not jobs and not copies:
        sys.exit("Nothing matched. Check --src and --packs.")

    totals = Totals()
    mark_scales(jobs)
    if args.split_heads is not None:
        mark_splits(jobs, args.split_heads)
    if not args.force and not args.scan_materials:
        current = [job for job in jobs if is_current(job)]
        totals.skipped = len(current)
        jobs = [job for job in jobs if not is_current(job)]

    packs = sorted({pack_of(job.src, source_root) for job in jobs + copies})
    print(f"Source  {source_root}")
    print(f"Output  {output_root}")
    print(f"Packs   {', '.join(packs)}")
    print(f"Models  {len(jobs)} to convert, {totals.skipped} up to date")
    print(f"Assets  {len(copies)} files to mirror\n")

    if args.dry_run:
        for job in jobs[:20]:
            print(f"  {job.src.relative_to(source_root)} -> {job.dst.relative_to(output_root)}")
        if len(jobs) > 20:
            print(f"  ... and {len(jobs) - 20} more")
        return

    blender = find_blender(args.blender)
    started = time.monotonic()
    contexts, override_warnings = pack_contexts(packs, source_root, output_root)
    for warning in override_warnings:
        print(f"  material_overrides.json: {warning}")
    if not args.scan_materials:
        copy_assets(copies, args.force, totals)
    if jobs:
        options = {"verify": args.verify, "vertex_colors": args.vertex_colors,
                   "animations": args.animations, "materials": args.materials,
                   "lods": args.lods, "scan_only": args.scan_materials,
                   "scan_report": bool(args.scan_report),
                   "skip_untextured": args.skip_untextured}
        convert_all(blender, jobs, options, contexts,
                    max(1, min(args.workers, len(jobs))), totals, args.quiet)

    if args.scan_materials:
        if args.scan_report:
            args.scan_report.parent.mkdir(parents=True, exist_ok=True)
            args.scan_report.write_text(
                json.dumps({"models": totals.scanned}, indent=2) + "\n", encoding="utf-8")
            print(f"  wrote {args.scan_report} ({len(totals.scanned)} models)")
        report_materials(totals)
        print(f"\nScanned {totals.converted} files in {time.monotonic() - started:.1f}s. "
              f"Nothing was written.")
        sys.exit(1 if totals.failed else 0)

    report(totals, time.monotonic() - started)
    if args.skip_untextured and totals.skipped:
        # Whether a model is untextured is only known once it has been through Blender, so
        # one the incremental check never handed over cannot have been judged.
        print(f"\n  Note: {totals.skipped} model(s) were already up to date and so were never "
              f"examined,\n  which means any untextured ones among them are still in the output."
              f"\n  Rerun with --force to apply --skip-untextured across the whole tree.")
    report_scale(totals)
    report_materials(totals)
    if args.split_heads:
        # Only worth saying when names were given: without them most files are props and
        # holding no character is the expected answer, not a problem.
        missed = sorted({str(job.src) for job in jobs if job.split} - set(totals.split))
        if missed:
            print(f"\n{len(missed)} named file(s) held no rigged character to split:")
            for path in missed[:10]:
                print(f"  {Path(path).name}")
    if args.materials == "external":
        materials_root = args.materials_dir.resolve()
        prefix = args.res_prefix or f"res://{output_root.name}"
        written = write_manifests(totals, materials_root, output_root, prefix)
        for target, count in written:
            print(f"  wrote {target} ({count} materials)")
        if not written and totals.skipped:
            # Material records come from reading the FBX, so nothing to rebuild from.
            print(f"\n  Note: every model was already up to date, so no manifest was rewritten."
                  f"\n  Rerun with --force to regenerate them (needed after changing --res-prefix).")
        if written:
            assets_at = prefix.removeprefix("res://").rstrip("/")
            tools = Path(__file__).parent / "tools"
            print("\nTo use these, copy these three folders into your Godot project:")
            print(f"  {output_root}  ->  <project>/{assets_at}/")
            print(f"  {materials_root}  ->  <project>/materials/")
            print(f"  {tools}  ->  <project>/tools/")
            # Textures must be in Godot's import cache before the generator can load them.
            print("\nthen, from that project:")
            print("  godot --headless --import")
            print("  godot --headless --script res://tools/generate_materials.gd")
            print(f"\nThe materials reference textures at {prefix}/..., so the assets have to "
                  f"land there.\nFor a different location, reconvert with "
                  f"--res-prefix res://your/path --force.")
    sys.exit(1 if totals.failed else 0)


if __name__ == "__main__":
    main()
