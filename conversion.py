"""Discovery, per-pack context and the pool of Blender processes that does the work.

Conversion is a single pass. Each worker resolves textures itself, because resolution
depends only on the reference name and the pack's shipped texture list, both knowable
locally. Workers report what they saw and the CLI dedupes those records across all of them
to write one manifest per pack.
"""

from __future__ import annotations

import collections
import fnmatch
import json
import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import material_flavors
import texture_matching
from reporting import human

RESULT_PREFIX = "@@RESULT "
WORKER_SCRIPT = Path(__file__).with_name("blender_convert.py")
OVERRIDES_FILE = Path(__file__).with_name("texture_overrides.json")
SCALES_FILE = Path(__file__).with_name("scale_overrides.json")
FOLIAGE_FILE = Path(__file__).with_name("foliage_overrides.json")
MATERIALS_FILE = Path(__file__).with_name("material_overrides.json")

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
    # Models that arrived as ASCII FBX and were transcoded before import. Counted rather
    # than warned about: it is what the converter does with a file Synty shipped in the
    # wrong format, and the count is how you notice a pack has more of them than it used to.
    repaired: int = 0
    grew: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    materials: dict = field(default_factory=dict)
    split: dict = field(default_factory=dict)
    lods: dict = field(default_factory=dict)
    foliage: dict = field(default_factory=dict)
    untextured: dict = field(default_factory=dict)
    # Flavor fills counted per model, before merging. A filled material renames itself after
    # its new texture and merges with any identically named one that resolved normally, so
    # by the time totals.materials exists the fill may be invisible.
    filled: collections.Counter = field(default_factory=collections.Counter)
    # Raw per-model scan records, kept only when --scan-report asks for them.
    scanned: list = field(default_factory=list)
    spans: dict = field(default_factory=lambda: collections.defaultdict(list))


def find_blender(explicit):
    for candidate in filter(None, [explicit, os.environ.get("BLENDER")] + BLENDER_CANDIDATES):
        resolved = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if resolved:
            return resolved
    raise SystemExit("Blender not found. Pass --blender <path> or set the BLENDER "
                     "environment variable.")


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
        pack = pack_of(path, source_root)
        if patterns and not any(fnmatch.fnmatch(pack, f"*{p}*") for p in patterns):
            continue
        relative = path.relative_to(source_root)
        if path.suffix.lower() == ".fbx":
            jobs.append(Job(path, output_root / relative.with_suffix(".glb"), pack))
        else:
            copies.append(Job(path, output_root / relative, pack))
    return jobs, copies


def mark_splits(jobs, patterns):
    """Flag the jobs whose characters should have their heads split onto their own node.

    An empty pattern list offers every job up; whether a file holds a character is decided
    in Blender by looking for a rig with a head bone, since a name cannot tell you that.
    """
    for job in jobs:
        job.split = not patterns or any(fnmatch.fnmatch(job.src.name, f"*{p}*") for p in patterns)


def mark_scales(jobs):
    """Attach the unit-scale correction each job's pack needs, if it needs one.

    An FBX declares the unit its geometry is in and the importer converts from it, so
    normally there is nothing to correct. See scale_overrides.json for the packs that
    declare the wrong one.
    """
    if not SCALES_FILE.exists():
        return
    overrides = json.loads(SCALES_FILE.read_text(encoding="utf-8"))
    for job in jobs:
        entry = overrides.get(job.pack)
        if not entry:
            continue
        # A pack-wide scale with per-file exceptions, since a pack that gets this wrong
        # tends to get it wrong for most files rather than all of them.
        job.scale = next((value for pattern, value in entry.get("files", {}).items()
                          if fnmatch.fnmatch(job.src.stem, pattern)), entry.get("scale", 1.0))


def load_overrides(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def pack_contexts(packs, source_root, output_root):
    """Per-pack texture index and curated overrides, shared with every worker.

    Returns the contexts and any complaint the override files made about themselves, which
    the caller prints: a curated table that has silently stopped matching is the main way
    these files rot.
    """
    overrides = load_overrides(OVERRIDES_FILE)
    foliage = load_overrides(FOLIAGE_FILE)
    flavors = load_overrides(MATERIALS_FILE)
    indexes = {}
    warnings = []

    def index(pack):
        if pack not in indexes:
            indexes[pack] = texture_matching.index_textures(source_root / pack)
        return indexes[pack]

    def declared(table, pack):
        return {k: v for k, v in table.get(pack, {}).items() if not k.startswith("_")}

    contexts = {}
    for pack in packs:
        entries = declared(overrides, pack)
        # An override may point into another pack, which then has to be indexed too,
        # whether or not that pack is itself part of this run.
        foreign = {}
        for target in entries.values():
            if texture_matching.FOREIGN_SEPARATOR in target:
                other = target.split(texture_matching.FOREIGN_SEPARATOR, 1)[0]
                foreign[other] = index(other)
        config = declared(flavors, pack)
        pack_root = str(source_root / pack)
        complaints = []
        sets = material_flavors.expand_sets(config, index(pack), pack_root, complaints)
        contexts[pack] = {
            "source_root": pack_root,
            "output_root": str(output_root / pack),
            "textures": index(pack),
            "overrides": entries,
            "foreign": foreign,
            "foliage": declared(foliage, pack),
            "materials": {
                "sets": sets,
                "bind": material_flavors.normalize_bindings(config, sets, complaints),
                "companions": material_flavors.expand_companions(config, index(pack),
                                                                 pack_root, complaints),
            },
        }
        warnings.extend(f"{pack}: {complaint}" for complaint in complaints)
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


def accumulate(result, options, totals):
    """Fold one worker result into the running totals. Caller holds the lock."""
    # Ahead of both early returns below, since a repaired model can equally be one a scan
    # only looked at or one that gets dropped as untextured.
    if result.get("repaired"):
        totals.repaired += 1
    if options.get("scan_report"):
        # Bounds travel with the record because authoring a scale override needs a
        # per-model size and nothing else reports one. World space, so a node scale the
        # geometry already carries is included, which reading the mesh alone misses.
        totals.scanned.append({"src": result.get("src"), "pack": result.get("pack", ""),
                               "bounds": (result.get("summary") or {}).get("bounds"),
                               "materials": result.get("materials", [])})
    if result.get("untextured"):
        # Nothing was written and no material survives to describe, so this contributes
        # neither bytes nor a manifest entry.
        totals.untextured[result["src"]] = result.get("pack", "")
        return
    pack = result.get("pack", "")
    totals.converted += 1
    totals.src_bytes += result["src_bytes"]
    totals.dst_bytes += result["dst_bytes"]
    known = totals.materials.setdefault(pack, {})
    for material in result.get("materials", []):
        entry = known.setdefault(material["name"], dict(material, used_by=0, sources=set()))
        entry["used_by"] += 1
        entry["sources"].add(material["source"])
    # From the worker's pre-dedup fill list rather than result["materials"]: a fill whose
    # material merged into an identically named one would otherwise be invisible here.
    for fill in result.get("fills", []):
        totals.filled[(pack, fill["binding_model"], fill["binding"],
                       fill["name"], fill["flavor"])] += 1
    if result.get("split"):
        totals.split[result["src"]] = result["split"]
    if result.get("dropped_lods"):
        totals.lods[result["src"]] = result["dropped_lods"]
    if result.get("foliage"):
        totals.foliage[result["src"]] = result["foliage"]
    bounds = (result.get("summary") or {}).get("bounds")
    if bounds:
        totals.spans[pack].append(max(bounds[i + 3] - bounds[i] for i in range(3)))
    if result["dst_bytes"] >= result["src_bytes"]:
        totals.grew.append((result["src"], result["src_bytes"], result["dst_bytes"]))
    for warning in result.get("warnings", []):
        totals.warnings.append(f"{Path(result['src']).name}: {warning}")
    check = result.get("verify")
    if check and not check.get("ok"):
        totals.warnings.append(f"{Path(result['src']).name}: verification mismatch {check}")


def convert_all(blender, jobs, options, contexts, workers, totals, quiet):
    done = 0
    total = len(jobs)
    # Every worker thread reports through here, and each accumulation is a non-atomic
    # read-modify-write. The work is trivial next to a Blender conversion, so one lock
    # costs nothing and makes every counter trustworthy.
    lock = threading.Lock()

    def record(result):
        nonlocal done
        with lock:
            done += 1
            if not result.get("ok"):
                totals.failed += 1
                totals.failures.append((result.get("src"), result.get("pack", ""),
                                        result.get("error", "").strip().splitlines()[-1:]))
                print(f"  [{done}/{total}] FAILED {Path(result.get('src', '?')).name}")
                return
            accumulate(result, options, totals)
            if not quiet and (done % 25 == 0 or done == total):
                ratio = 100.0 * (1 - totals.dst_bytes / max(totals.src_bytes, 1))
                print(f"  [{done}/{total}] {human(totals.src_bytes)} -> "
                      f"{human(totals.dst_bytes)} ({ratio:.1f}% smaller)")

    batches = chunk(jobs, workers)
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        for batch in batches:
            pool.submit(run_worker, blender, batch, options, contexts, record)
