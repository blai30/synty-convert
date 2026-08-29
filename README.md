# Synty FBX to Godot GLB converter

Converts [Synty](https://syntystore.com) asset packs from FBX into GLB that drops straight into a Godot 4 project: correct scale, correct axes, shared materials, and a large reduction in file size.

The output mirrors the source tree exactly. Only `.fbx` becomes `.glb`; textures, licenses and everything else are copied through untouched.

```
synty_packs_fbx/POLYGON_BattleRoyale_Source_Files_v4/Source Files/FBX/SM_Bld_House_01.fbx
assets/         POLYGON_BattleRoyale_Source_Files_v4/Source Files/FBX/SM_Bld_House_01.glb
```

**This repo is a converter, not a Godot project.** It produces folders you copy into your own project. It ships no Synty content: you supply the packs you own.

## What it fixes

Synty FBX do not import cleanly into Godot on their own.

- **Scale and axes.** The packs are authored in Maya in centimeters, Y-up. A naive conversion gives you a node scaled to 1/100 and rotated 90 degrees, which throws off every `BoneAttachment3D`, collision shape and root motion value. The converter bakes that away, so a character arrives 1.79 m tall, upright, at Y = 0, on identity transforms.
- **Broken texture references.** Every material points at internal authoring files that were never shipped, usually named for a different pack. The converter works out which shipped texture each one meant.
- **Duplicated textures.** Synty FBX embed their atlas, so a naive conversion copies a 2048x2048 PNG into every model. The converter references one shared file instead.
- **Missing material channels.** Emissive and normal maps that ship in a pack but that no FBX references, because that wiring lived in Unity materials. A curated table puts them back.
- **Untextured models.** Whole buildings and trees export with their material bindings stripped and arrive flat white. Curated flavor sets and foliage bindings fix these.
- **Authoring leftovers.** Static `Take 001` animations and Unreal `UCX_` collision hulls are dropped. Left in, the take reapplies the transform normalization exists to remove, and the hull arrives as a visible untextured box.
- **Stacked LOD levels.** Godot cannot read an FBX LOD chain, so it renders every level at once. Only the finest is kept.
- **ASCII FBX.** Blender's importer reads binary only, and a handful of Synty models ship as ASCII. Those are transcoded before import.

Across BattleRoyale and Base Locomotion, 1039 models go from 528.7 MB to 152.9 MB, a 71% reduction.

## Requirements

|         |              |                                                      |
| ------- | ------------ | ---------------------------------------------------- |
| Python  | 3.9 or newer | standard library only, nothing to install            |
| Blender | 4.x or 5.x   | does the conversion, run headless. Tested on 5.2 LTS |
| Godot   | 4.x          | only for generating materials. Tested on 4.8         |

Blender is found on `PATH`, via the `BLENDER` environment variable, or at the usual install locations. Point at it explicitly with `--blender` if needed.

## Setup

Put each pack in its own folder under `synty_packs_fbx/`, exactly as it comes out of the Synty download. The internal layout does not matter; packs variously use `FBX/`, `Models/`, `Source Files/`, `SourceFiles/` and others, and all of them work.

```
synty_packs_fbx/
  POLYGON_BattleRoyale_Source_Files_v4/
    Source Files/
      FBX/         *.fbx
      Textures/    *.png, *.tga
  PolygonFantasyKingdom/
    FBX/           *.fbx
    Textures/      *.png
```

The folder name of each pack is what `--packs` matches and what the output is keyed on.

## Quickstart

```bash
# 1. Optional: preview how textures will resolve, writing nothing
python synty_convert.py --scan-materials --packs PolygonFantasyKingdom

# 2. Convert everything under synty_packs_fbx/
python synty_convert.py

# 3. Optional: check the output
python audit.py
```

Reruns are incremental: a model whose `.glb` is newer than its `.fbx` is skipped. Use `--force` to reconvert regardless. Add `--verify` to reimport every GLB and check bounds, bone count, action count and mesh count against the source; it roughly doubles the runtime and is worth it on a first run.

The run ends with a per-pack material summary. Anything listed as `UNRESOLVED` becomes a color-only material. See [docs/materials.md](docs/materials.md) to fix those.

### Getting the result into Godot

Copy three folders into your project. `tools/` holds the Godot side scripts, which have to live inside the project for Godot to run them.

```bash
cp -r assets    /path/to/YourGame/assets
cp -r materials /path/to/YourGame/materials
cp -r tools     /path/to/YourGame/tools
```

```powershell
Copy-Item -Recurse .\assets    C:\path\to\YourGame\assets
Copy-Item -Recurse .\materials C:\path\to\YourGame\materials
Copy-Item -Recurse .\tools     C:\path\to\YourGame\tools
```

Then, from that project:

```bash
godot --headless --import
godot --headless --script res://tools/generate_materials.gd
```

The import pass is not optional. The generator loads each texture through Godot, so they have to be in its import cache first; skip it and every material comes out untextured.

To put the assets somewhere other than `res://assets`, say so at conversion time, because the destination is baked into the manifests: `--res-prefix res://addons/synty/assets --force`. Point the generator at a relocated `materials/` with `-- --materials res://your/path`.

## Using the assets in Godot

**Models work immediately.** Drag any `.glb` into a scene. It renders with the correct atlas at real world scale, Y-up, feet at Y = 0. Each GLB carries its own material pointing at the shared texture file.

**The `.tres` materials are an opt-in upgrade.** Godot creates a separate material instance per imported scene, so 247 models sharing one atlas produce 247 materials. Pointing them at a single resource gives you one material RID, which batches better, and one place to edit. To apply one: select the `.glb`, open the **Import** dock, click **Advanced...**, pick the material, tick **Use External**, choose the `.tres`, and **Reimport**.

Animation packs import as a `Skeleton3D` plus an `AnimationPlayer` holding one clip. Synty's animation packs target their own skeletons, so check bone names match before retargeting.

## Command reference

| Option               | Default            | Purpose                                                       |
| -------------------- | ------------------ | ------------------------------------------------------------- |
| `--src`              | `synty_packs_fbx`  | Where the packs live                                          |
| `--dst`              | `assets`           | Where converted packs are written                             |
| `--packs`            | all                | Only packs whose folder name contains one of these substrings |
| `--materials`        | `external`         | `none` strips materials for barebones meshes                  |
| `--materials-dir`    | `materials`        | Where the per-pack manifests are written                      |
| `--res-prefix`       | `res://<dst name>` | Where the assets will live in the target Godot project        |
| `--split-heads`      | off                | Put each character's head on its own mesh node                |
| `--untextured MODE`  | `fill`             | `fill` uses the flavor set default, `keep` leaves flat color, `drop` writes no model, `fill-or-drop` does both |
| `--scan-materials`   | off                | Report texture resolution only, write nothing                 |
| `--scan-report PATH` | off                | With `--scan-materials`, dump every model's raw material records to `PATH` |
| `--force`            | off                | Reconvert files that are already up to date                   |
| `--verify`           | off                | Reimport each GLB and check bounds, bone and action parity    |
| `--vertex-colors`    | `drop`             | `keep` retains color attributes                               |
| `--animations`       | `keep`             | `drop` discards baked-in takes                                |
| `--lods`             | `drop`             | `keep` ships every LOD level, which renders them all at once  |
| `-j`, `--workers`    | half the CPU cores | Concurrent Blender processes                                  |
| `--dry-run`          | off                | List what would happen and exit                               |
| `--quiet`            | off                | Only print the final summary                                  |
| `--blender`          | autodetect         | Path to the Blender executable                                |

`generate_materials.gd` takes `-- --materials res://path`. `verify_import.gd` takes `-- --assets res://path`.

## Documentation

| | |
| --- | --- |
| [docs/materials.md](docs/materials.md) | How textures resolve, and the three curated override files that handle what a matcher cannot |
| [docs/geometry.md](docs/geometry.md) | Unit scale corrections, LOD chains, foliage bindings and splitting character heads |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Symptoms and their fixes |
| [docs/DESIGN.md](docs/DESIGN.md) | How the conversion works and why it makes the choices it does |

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py" -t tests
```

Pure Python, no Blender needed. They cover the parts that decide what a model ends up wearing: texture matching, material naming, flavor sets, companion maps, the ASCII FBX parser and the reports.

## What is committed

Nothing this repo generates. `assets/` and `materials/` are gitignored, since both are reproducible by rerunning the converter. The packs are ignored too: `synty_packs_fbx/` keeps only a `.gitkeep`. What is tracked is the tool itself, plus the curated override files you are meant to hand edit.

In **your Godot project** the opposite applies. Commit `materials/` there: it is small, and the `.tres` are where you would tune things like `texture_filter` for atlas bleed. Rerunning the generator overwrites them; reconverting models does not.

## License

[MIT](LICENSE) for the converter. The Synty asset packs it converts are not part of this repo and stay under [Synty's own license](https://syntystore.com/pages/end-user-licence-agreement); nothing here grants any rights to them.
