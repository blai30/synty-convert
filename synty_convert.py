"""Convert Synty asset packs from FBX to Godot-ready GLB.

Mirrors the source tree into the output directory, replacing every ``.fbx`` with a ``.glb``
and copying all other files (textures, licenses) untouched. Materials reference the pack's
shared texture atlas rather than embedding a copy of it.

    python synty_convert.py --packs POLYGON_BattleRoyale ANIMATION_Base

Conversion itself runs inside Blender; see ``blender_convert.py``. Work is spread over a
pool of Blender processes, each handling many files per launch so the startup cost is paid
once per worker rather than once per file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conversion
import manifests
import reporting


def build_parser(root):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=root / "synty_packs_fbx",
                        help="source pack directory")
    parser.add_argument("--dst", type=Path, default=root / "assets", help="output directory")
    parser.add_argument("--packs", nargs="*", default=None,
                        help="only convert packs whose folder name contains one of these "
                             "substrings")
    parser.add_argument("-j", "--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2),
                        help="number of concurrent Blender processes")
    parser.add_argument("--force", action="store_true",
                        help="reconvert files that are already up to date")
    parser.add_argument("--verify", action="store_true",
                        help="reimport each GLB and check vertex, bone and bounding-box parity")
    parser.add_argument("--vertex-colors", choices=("drop", "keep"), default="drop",
                        help="drop vertex colors for barebones meshes, or keep them")
    parser.add_argument("--animations", choices=("keep", "drop"), default="keep",
                        help="drop baked-in takes; useful for model packs whose clips live "
                             "elsewhere")
    parser.add_argument("--lods", choices=("drop", "keep"), default="drop",
                        help="drop every LOD level above the finest, since Godot builds its "
                             "own chain; 'keep' ships all of them, which renders them at once")
    parser.add_argument("--materials", choices=("external", "none"), default="external",
                        help="'external' references shared textures by URI and writes Godot "
                             "manifests; 'none' strips materials for barebones meshes")
    parser.add_argument("--materials-dir", type=Path, default=root / "materials",
                        help="where per-pack material manifests are written")
    parser.add_argument("--res-prefix", default=None,
                        help="res:// location the converted assets will live at in your Godot "
                             "project (default: res://<output folder name>)")
    parser.add_argument("--split-heads", nargs="*", default=None, metavar="NAME",
                        help="split every rigged character's head onto its own mesh node, so "
                             "it can be hidden or moved to another render layer in Godot; "
                             "name substrings limit this to matching files")
    parser.add_argument("--untextured", choices=("fill", "keep", "drop", "fill-or-drop"),
                        default="fill",
                        help="what to do with a material that bound no texture. 'fill' gives "
                             "it the default from its flavor set in material_overrides.json; "
                             "'keep' leaves it as flat color; 'drop' writes no model for it "
                             "at all; 'fill-or-drop' fills what it can and drops the rest. "
                             "Animation files, which carry no mesh, are never dropped")
    parser.add_argument("--scan-materials", action="store_true",
                        help="report how texture references resolve, without converting")
    parser.add_argument("--scan-report", type=Path, default=None, metavar="PATH",
                        help="with --scan-materials, also write every model's raw material "
                             "records to PATH as JSON; this is what the per-pack override "
                             "tables are authored from")
    parser.add_argument("--dry-run", action="store_true", help="list what would happen and exit")
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    parser.add_argument("--blender", default=None, help="path to the Blender executable")
    return parser


def worker_options(args):
    return {"verify": args.verify, "vertex_colors": args.vertex_colors,
            "animations": args.animations, "materials": args.materials, "lods": args.lods,
            "scan_only": args.scan_materials, "scan_report": bool(args.scan_report),
            "untextured": args.untextured}


def print_next_steps(output_root, materials_root, res_prefix):
    """How to get the output into a Godot project and turn the manifests into materials."""
    assets_at = res_prefix.removeprefix("res://").rstrip("/")
    print("\nTo use these, copy these three folders into your Godot project:")
    print(f"  {output_root}  ->  <project>/{assets_at}/")
    print(f"  {materials_root}  ->  <project>/materials/")
    print(f"  {Path(__file__).parent / 'tools'}  ->  <project>/tools/")
    # Textures must be in Godot's import cache before the generator can load them.
    print("\nthen, from that project:")
    print("  godot --headless --import")
    print("  godot --headless --script res://tools/generate_materials.gd")
    print(f"\nThe materials reference textures at {res_prefix}/..., so the assets have to "
          f"land there.\nFor a different location, reconvert with "
          f"--res-prefix res://your/path --force.")


def main():
    root = Path(__file__).resolve().parent
    args = build_parser(root).parse_args()

    source_root = args.src.resolve()
    output_root = args.dst.resolve()
    if not source_root.is_dir():
        sys.exit(f"Source directory not found: {source_root}")
    if args.untextured in ("drop", "fill-or-drop") and args.materials == "none":
        # --materials none turns the external-materials path off entirely, so nothing would
        # ever bind a texture and drop would delete every model outright.
        sys.exit(f"--untextured {args.untextured} needs materials to judge; "
                 f"it cannot be used with --materials none.")
    if args.scan_report and not args.scan_materials:
        sys.exit("--scan-report only has anything to write during --scan-materials.")

    jobs, copies = conversion.discover(source_root, output_root, args.packs)
    if not jobs and not copies:
        sys.exit("Nothing matched. Check --src and --packs.")

    totals = conversion.Totals()
    conversion.mark_scales(jobs)
    if args.split_heads is not None:
        conversion.mark_splits(jobs, args.split_heads)
    if not args.force and not args.scan_materials:
        totals.skipped = sum(1 for job in jobs if conversion.is_current(job))
        jobs = [job for job in jobs if not conversion.is_current(job)]

    packs = sorted({conversion.pack_of(job.src, source_root) for job in jobs + copies})
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

    blender = conversion.find_blender(args.blender)
    started = time.monotonic()
    contexts, override_warnings = conversion.pack_contexts(packs, source_root, output_root)
    for warning in override_warnings:
        print(f"  material_overrides.json: {warning}")
    if not args.scan_materials:
        conversion.copy_assets(copies, args.force, totals)
    if jobs:
        conversion.convert_all(blender, jobs, worker_options(args), contexts,
                               max(1, min(args.workers, len(jobs))), totals, args.quiet)

    # A DEAD line claims a binding matched nothing. Only a run that could have watched it
    # fire may say so: keep and drop never hand the worker a binding table, and an
    # incremental run only examined the models it reconverted.
    judge = args.untextured in ("fill", "fill-or-drop") and not totals.skipped

    if args.scan_materials:
        if args.scan_report:
            args.scan_report.parent.mkdir(parents=True, exist_ok=True)
            args.scan_report.write_text(
                json.dumps({"models": totals.scanned}, indent=2) + "\n", encoding="utf-8")
            print(f"  wrote {args.scan_report} ({len(totals.scanned)} models)")
        reporting.report_materials(totals, contexts, judge)
        reporting.report_failures(totals)
        print(f"\nScanned {totals.converted} files in {time.monotonic() - started:.1f}s"
              f"{f', {totals.failed} failed' if totals.failed else ''}. Nothing was written.")
        sys.exit(1 if totals.failed else 0)

    reporting.report(totals, time.monotonic() - started)
    if args.untextured in ("drop", "fill-or-drop") and totals.skipped:
        # Whether a model is untextured is only known once it has been through Blender.
        print(f"\n  Note: {totals.skipped} model(s) were already up to date and so were never "
              f"examined,\n  which means any untextured ones among them are still in the "
              f"output.\n  Rerun with --force to apply --untextured {args.untextured} across "
              f"the whole tree.")
    reporting.report_scale(totals)
    reporting.report_materials(totals, contexts, judge)
    if args.split_heads:
        # Only worth saying when names were given: without them most files are props, and
        # holding no character is the expected answer rather than a problem.
        missed = sorted({str(job.src) for job in jobs if job.split} - set(totals.split))
        if missed:
            print(f"\n{len(missed)} named file(s) held no rigged character to split:")
            for path in missed[:10]:
                print(f"  {Path(path).name}")

    if args.materials == "external":
        materials_root = args.materials_dir.resolve()
        res_prefix = args.res_prefix or f"res://{output_root.name}"
        written = manifests.write_manifests(totals, materials_root, output_root,
                                            res_prefix, contexts)
        for target, count in written:
            print(f"  wrote {target} ({count} materials)")
        if written:
            print_next_steps(output_root, materials_root, res_prefix)
        elif totals.skipped:
            # Material records come from reading the FBX, so nothing to rebuild from.
            print(f"\n  Note: every model was already up to date, so no manifest was "
                  f"rewritten.\n  Rerun with --force to regenerate them (needed after "
                  f"changing --res-prefix).")
    sys.exit(1 if totals.failed else 0)


if __name__ == "__main__":
    main()
