# Synty FBX to Godot GLB converter

Converts Synty asset packs from FBX into GLB that drops straight into a Godot 4 project: correct scale, correct axes, shared materials, and a large reduction in file size.

The output tree mirrors the source exactly. Only `.fbx` becomes `.glb`; textures, licenses and everything else are copied through untouched, with the folder layout preserved.

```
synty_packs_fbx/POLYGON_BattleRoyale_Source_Files_v4/Source Files/Models/SM_Bld_House_01.fbx
assets/         POLYGON_BattleRoyale_Source_Files_v4/Source Files/Models/SM_Bld_House_01.glb
```

**This repo is a converter, not a Godot project.** It produces folders that you copy into your own project.

```
synty_convert.py       the converter
audit.py               checks the output
texture_matching.py    resolves texture references     (used by the converter)
blender_convert.py     runs inside Blender             (used by the converter)
split_character_head.py splits character heads         (used by blender_convert.py)
texture_overrides.json manual texture mappings
scale_overrides.json   unit corrections for packs that declare the wrong one

tools/                 Godot side scripts, copied into your project as <project>/tools/
synty_packs_fbx/       put the packs here
assets/                output: converted packs         -> <project>/assets/
materials/             output: material manifests      -> <project>/materials/
```

## What it fixes

Synty FBX do not import cleanly into Godot on their own. The converter deals with five problems:

- **Scale and axes.** The packs are authored in Maya in centimeters, Y-up. A naive conversion gives you a `Node3D` or `Skeleton3D` scaled to 1/100 and rotated 90 degrees, which throws off every `BoneAttachment3D`, collision shape and root motion value. The converter bakes that away, so a character arrives 1.79 m tall, upright, standing at Y = 0, on identity transforms. A few packs declare a unit their geometry is not actually in; see [Fixing a pack that converts too small](#fixing-a-pack-that-converts-too-small).
- **Broken texture references.** Every material points at internal authoring files that were never shipped, usually named for a different pack. The converter works out which shipped texture each one meant.
- **Duplicated textures.** Synty FBX embed their atlas, so a naive conversion copies a 2048x2048 PNG into every model. The converter references one shared file instead.
- **Authoring leftovers.** Some models carry a single-key `Take 001` that only restates the import transform, or an Unreal `UCX_` collision hull. Both are dropped. Left in, the take blocks normalization and then reapplies in Godot the very transform normalization exists to remove, and the hull arrives as a visible untextured box over the prop it was meant to bound.
- **Size.** 1039 models across two packs go from 528.7 MB to 152.9 MB, a 71.1% reduction.

## Requirements

|         |              |                                                      |
| ------- | ------------ | ---------------------------------------------------- |
| Python  | 3.9 or newer | standard library only, nothing to install            |
| Blender | 4.x or 5.x   | does the conversion, run headless. Tested on 5.2 LTS |
| Godot   | 4.x          | only for generating materials. Tested on 4.8         |

Blender is found on `PATH`, via the `BLENDER` environment variable, or at the usual install locations. Point at it explicitly with `--blender` if needed.

## Setup

Put each pack in its own folder under `synty_packs_fbx/`, exactly as it comes out of the Synty download. The internal layout does not matter; packs variously use `Models/`, `Source Files/`, `SourceFiles/`, `Source_Files/` and others, and all of them work.

```
synty_packs_fbx/
  POLYGON_BattleRoyale_Source_Files_v4/
    Source Files/
      Models/      *.fbx
      Characters/  *.fbx
      Textures/    *.png, *.tga
  PolygonFantasyKingdom/
    Models/        *.fbx
    Textures/      *.png
```

The folder name of each pack is what `--packs` matches and what the output and material folders are keyed on.

## Workflow

```
1. scan     preview how textures resolve            (optional, writes nothing)
2. convert  fbx -> glb + material manifests
3. review   check the report, add overrides         (optional)
4. copy     assets/, materials/ and tools/ into your project
5. import   let Godot import them
6. generate Godot authors the .tres materials
```

### 1. Preview how textures will resolve

Optional but recommended for a pack you have not converted before. This reads the FBX and reports what each material would resolve to, without writing anything.

```bash
python synty_convert.py --scan-materials --packs PolygonFantasyKingdom
```

```
PolygonFantasyKingdom: 20 materials  (1 exact, 0 override, 2 heuristic, 9 unresolved, 8 untextured)
   review  PolygonCastle_Texture_01_A.psd -> PolygonFantasyKingdom_01_A (1896 files)
   review  PolygonFantasyKingdon_Texture_Wall_Brick_01.png -> Wall_Brick_01 (1 files)
   UNRESOLVED  Horse_Texture_01.psd  (colour only; add to texture_overrides.json)
   UNRESOLVED  WALL.bmp  (colour only; add to texture_overrides.json)
```

`review` lines resolved by heuristic and are worth a glance. `UNRESOLVED` means no confident match was found, so that material will carry colour only. See [Fixing unresolved textures](#fixing-unresolved-textures).

### 2. Convert

```bash
# one or more packs, matched as substrings of the folder name
python synty_convert.py --packs POLYGON_BattleRoyale ANIMATION_Base_Locomotion

# or everything under synty_packs_fbx/
python synty_convert.py
```

Reruns are incremental: a model whose `.glb` is newer than its `.fbx` is skipped, so adding a pack only converts the new pack. Use `--force` to reconvert regardless.

Add `--verify` to reimport every GLB and check bounds, bone count, action count and mesh count against the source. It roughly doubles the runtime and is worth it on a first run.

This produces:

```
assets/     mirrored packs, .fbx replaced by .glb, textures copied through
materials/  one materials.json per pack
```

### 3. Review the report

The run ends with a per-pack material summary. Anything listed as `UNRESOLVED` becomes a colour-only material. Fix those now if you want them textured, then rerun with `--force`.

### 4. Copy into your Godot project

Copy three folders in. `tools/` holds the Godot side scripts, which have to live inside the project for Godot to run them. By default the materials expect the assets at `res://assets`:

```bash
cp -r assets    /path/to/YourGame/assets
cp -r materials /path/to/YourGame/materials
cp -r tools     /path/to/YourGame/tools
```

```powershell
# PowerShell
Copy-Item -Recurse .\assets    C:\path\to\YourGame\assets
Copy-Item -Recurse .\materials C:\path\to\YourGame\materials
Copy-Item -Recurse .\tools     C:\path\to\YourGame\tools
```

To put them somewhere else, say so at conversion time, because the destination is baked into the manifests:

```bash
python synty_convert.py --res-prefix res://addons/synty/assets --force
```

`--force` is required there. Manifests are built while reading the FBX, so an up-to-date run has nothing to rebuild them from. The tool tells you if this happens.

### 5. Import, then 6. generate the materials

Run both from your Godot project:

```bash
godot --headless --import
godot --headless --script res://tools/generate_materials.gd
```

```
POLYGON_BattleRoyale_Source_Files_v4: 18 materials
Wrote 20 materials, 0 failed, 0 missing textures.
```

The import pass is not optional. The generator loads each texture through Godot, so they have to be in its import cache first; skip it and every material comes out untextured.

One `.tres` is written next to each manifest. If `materials/` landed somewhere other than `res://materials`, point the generator at it:

```bash
godot --headless --script res://tools/generate_materials.gd -- --materials res://addons/synty/materials
```

If your `godot` command is a wrapper script, check whether it changes the working directory. Pass an absolute `--path /path/to/YourGame` if so.

## Using the assets in Godot

**Models work immediately.** Drag any `.glb` into a scene. It renders with the correct atlas at real world scale, Y-up, feet at Y = 0. Each GLB carries its own material pointing at the shared texture file, so nothing further is needed.

**The `.tres` materials are an opt-in upgrade.** Godot creates a separate material instance per imported scene, so 247 models sharing one atlas produce 247 materials. Pointing them at a single resource gives you one material RID, which batches better, and one place to edit.

To apply one, per model:

1. Select the `.glb` in the FileSystem dock
2. Open the **Import** dock, click **Advanced...**
3. Pick the material under **Materials** in the left panel
4. Tick **Use External**, choose the `.tres`
5. **Reimport**

Animation packs import as a `Skeleton3D` plus an `AnimationPlayer` holding one clip. To drive a character with them, both must share a rig; Synty's animation packs target their own skeletons, so check bone names match before retargeting.

## Splitting character heads

Synty characters are a single skinned mesh with the head welded into the body, so there is nothing to toggle if you want to hide it. `--split-heads` puts the head on its own mesh node instead:

```bash
# every rigged character in every pack
python synty_convert.py --split-heads --force

# only the ones you name, matched as substrings of the filename
python synty_convert.py --split-heads SK_Chr_MilitaryMale_01 SK_Character_Cop_01 --force
```

`--force` is needed on an existing conversion, since a model whose `.glb` is already up to date is skipped and would never be rebuilt.

The GLB still lands at its normal path with the same name. Inside it, one mesh node becomes two under the same `Skeleton3D`:

```
Skeleton3D
  Character_MilitaryMale_01_Body   MeshInstance3D
  Character_MilitaryMale_01_Head   MeshInstance3D
```

Which files get split is decided by content, not by name: a single armature carrying a `Head` bone. Props flow through untouched, so it is safe to leave the flag on for a whole run. Names only narrow the set.

The head is chosen by vertex weight, counting the head bone plus every bone parented under it. That closure is what carries the eyes and eyebrows across, since they are weighted to their own bones rather than to the head.

Faces that span the boundary stay with the body, so the seam vertices exist in both halves at matching positions with matching weights and the neck cannot crack open under a pose. That leaves the body itself open at the neck, which is invisible while the head is shown and a hole through the torso once it is hidden, so the body is capped with a triangle fan whose center vertex carries the neck's mean weights and deforms with it. The head is deliberately left open, because its opening sits inside the body's cap and capping both would leave two coincident faces to z-fight.

### Using it for first person

Both halves are ordinary nodes, so hiding the head is `$Head.visible = false`. Two things are worth doing instead:

```gdscript
# your own camera skips the head; every other camera still sees it
head.layers = 2
first_person_camera.cull_mask &= ~2

# and your shadow keeps its head
head.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
```

The render layer is per camera rather than per instance, so other players, your third person toggle, spectator and killcam views all keep the head with no state to synchronize. `SHADOWS_ONLY` matters because you see your own shadow constantly, and a headless one is a bug you would otherwise have no way to fix.

### Necks that cannot be capped

A handful of Synty meshes ship non-manifold around the neck, and a branching boundary is not a ring a fan can close. Those are counted on the summary line and left open rather than half filled:

```
Split     154 head(s) off 81 character model(s), 2 left open at the neck
```

Across all eleven packs here that is 2 of 154. Both are meshes whose vendor geometry is already broken before conversion. If a model you want as the player character shows up in that count, check it in Godot with the head hidden before building on it.

## Fixing unresolved textures

Synty's FBX reference authoring files that never shipped, so some references cannot be matched. When that happens the material keeps its colour and is reported.

Add the mapping to `texture_overrides.json`, keyed by pack folder name, then by the texture stem the FBX asks for. Values are path suffixes matched against the pack's shipped textures:

```json
{
  "POLYGON_BattleRoyale_Source_Files_v4": {
    "Air_Vehicle_Master_01": "Textures/PolygonBattleRoyale_Plane_01.png",
    "track2": "Textures/PolygonBattleRoyale_Tank_Tracks.png"
  }
}
```

Overrides beat the heuristic, so use them to correct a wrong match too. Then rerun the conversion with `--force` and regenerate the materials.

Sometimes the texture a pack asks for is one **another pack** ships. Synty's biome packs are built on the base Nature pack and reference its atlas directly, and the generic `SM_Generic_*` filler kit carries the Construction pack's atlas wherever it turns up. Name the other pack ahead of the suffix for those:

```json
{
  "POLYGON_NatureBiomes_MeadowForest_SourceFiles_v2": {
    "PolygonNature": "POLYGON_Nature_Source_Files_v2::Textures/PolygonNature_01.png"
  }
}
```

That other pack has to be converted too, since the material points at its mirrored copy under `assets/`. Convert only one of the pair and the run says so and leaves the material colour only.

The file ships with 47 mappings covering 14 packs, since these are facts about Synty's packs rather than anything project specific. They are the cases where a shipped texture is the unique, obvious counterpart, for example `PolygonScifi_Texture.psd` meaning `PolygonScifi_01_A.png`, or an artist's working copy like `PolygonWesternFrontier_Texture_Mike.psd`. A working file's name is not evidence of what it holds: `RopeBridge.png` is the atlas for 45 Meadow Forest props, none of which is a rope bridge, because the artist named the file in the Tropical Jungle scene that does have one. If you own a pack that is not listed, run `--scan-materials` and add what you find.

What is deliberately **not** mapped is anything ambiguous. `Wall_01.psd` in FantasyKingdom could be any of five shipped wall textures, and references to packs you do not own, like `PolygonAncientWorlds_Texture_01.png`, have no counterpart to find. Guessing would put a plausible but wrong texture on the model, which is harder to notice than an obviously untextured one, so those stay colour-only and stay in the report.

## Fixing a pack that converts too small

An FBX states the unit its geometry is in, and the converter converts from it. Some Synty packs state the wrong one: the geometry is in meters but the file says centimeters, so every model converts a hundred times under size. Nothing else catches this. The file is valid, the axes are right, the node transforms are identity, and the model is simply too small to see when you drag it into a scene.

The run says so:

```
POLYGON_Dungeon_Pack_SourceFiles_v2: the median model is 0.0245 m across, which means this
pack's FBX declare a unit their geometry is not in. Add a scale for it to
scale_overrides.json and reconvert with --force.
```

`scale` multiplies the conversion the FBX asks for and applies to the whole pack. `files` overrides that for filenames matching a glob, first match winning:

```json
{
  "POLYGON_Dungeon_Pack_SourceFiles_v2": {
    "scale": 100,
    "files": {
      "SM_Item_Chr_*": 1
    }
  }
}
```

Packs are rarely wrong about every file, which is what `files` is for. In the Dungeon pack 780 of 797 models are authored in meters, but the character-held items are genuinely in centimeters and two floor tiles carry a node scale that already compensates. Those seventeen arrive correct and are listed so the pack-wide correction skips them. The City pack splits the same way and along its folders: everything under `Models` is in meters, while the characters and vehicles are in centimeters.

A pack entry with only a `files` key goes the other way, for a pack that is fine apart from a handful of models. BattleRoyale, Nature, Dungeons Realms and Fantasy Kingdom each ship a few, and they are the harder ones to notice: one bridge out of four, one grass tuft out of a set, a candle flame.

The tell for a modular pack is its wall pieces: Synty builds on a 5 m grid, so a wall is 500 units in a centimeter pack and 5 in a meter one. For a single model, compare it against its own numbered siblings; `SM_Env_Bridge_01` is 11.00 x 5.00 x 2.79 units where `SM_Env_Bridge_02` is 1100 x 500 x 279, which is the same bridge authored a hundred times over. Both convert without complaint; only one is the right size.

## Verifying

```bash
python audit.py
```

Reads the GLB files back off disk and checks that no image is embedded, every image URI resolves to a real file, every root node is identity, UVs survived, and every manifest texture exists.

```
models 1039, image references 328 pointing at 12 distinct texture files
manifests 2, materials 20 (8 colour only), tres 0

PASS: no embedded images, every uri resolves, roots identity, UVs intact
```

An optional second check runs inside your Godot project and walks every imported model the way Godot actually sees it, asserting identity transforms, a material on every surface and shared textures. It ships in the same `tools/` folder:

```bash
godot --headless --script res://tools/verify_import.gd -- --assets res://assets
```

## Command reference

| Option             | Default            | Purpose                                                       |
| ------------------ | ------------------ | ------------------------------------------------------------- |
| `--src`            | `synty_packs_fbx`  | Where the packs live                                          |
| `--dst`            | `assets`           | Where converted packs are written                             |
| `--packs`          | all                | Only packs whose folder name contains one of these substrings |
| `--materials`      | `external`         | `none` strips materials for barebones meshes                  |
| `--materials-dir`  | `materials`        | Where the per-pack manifests are written                      |
| `--res-prefix`     | `res://<dst name>` | Where the assets will live in the target Godot project        |
| `--split-heads`    | off                | Put each character's head on its own mesh node                |
| `--scan-materials` | off                | Report texture resolution only, write nothing                 |
| `--force`          | off                | Reconvert files that are already up to date                   |
| `--verify`         | off                | Reimport each GLB and check bounds, bone and action parity    |
| `--vertex-colors`  | `drop`             | `keep` retains colour attributes                              |
| `--animations`     | `keep`             | `drop` discards baked-in takes                                |
| `-j`, `--workers`  | half the CPU cores | Concurrent Blender processes                                  |
| `--dry-run`        | off                | List what would happen and exit                               |
| `--quiet`          | off                | Only print the final summary                                  |
| `--blender`        | autodetect         | Path to the Blender executable                                |

`generate_materials.gd` takes `-- --materials res://path`. `verify_import.gd` takes `-- --assets res://path`.

## Troubleshooting

**`Wrote N materials, 0 failed, N missing textures`**
The textures are not in Godot's import cache. Run `godot --headless --import` first, and confirm the assets are at the `res://` location the manifests expect.

**Materials are untextured, or `.tres` reference a path that does not exist**
The assets did not land where the manifest expects. Check `albedo_texture` in `materials/<pack>/materials.json` against where you copied `assets/`. Reconvert with `--res-prefix` set correctly and `--force`.

**`No pack folder containing materials.json found`**
`materials/` is not at `res://materials`. Pass `-- --materials res://your/path`.

**Nothing was converted and no manifest was written**
Every model was already up to date. Rerun with `--force`, which is also required after changing `--res-prefix`.

**`Blender not found`**
Pass `--blender /path/to/blender` or set the `BLENDER` environment variable.

**A material lost its `_Alpha` suffix in Godot**
Expected. Godot treats `_Alpha` as a material name convention, strips it and applies transparency itself, so `PolygonBattleRoyale_Fence_Alpha` shows up as `PolygonBattleRoyale_Fence`. Cosmetic only.

**A model grew instead of shrinking**
Expected for small static props. See [Size expectations](docs/DESIGN.md#size-expectations).

**A whole pack arrives tiny in Godot**
Its FBX declare a unit the geometry is not in. See [Fixing a pack that converts too small](#fixing-a-pack-that-converts-too-small).

**Godot is importing my source FBX**
Keep `synty_packs_fbx/` outside your Godot project, or drop an empty `.gdignore` file in it.

## Notes

Nothing this repo generates is committed. `assets/` and `materials/` are gitignored, since both are reproducible by rerunning the converter, and both are created on demand. The packs are ignored too: `synty_packs_fbx/` keeps only a `.gitkeep`, so the folder is there to drop packs into without any of them being committed. What is tracked is the tool itself, plus `texture_overrides.json` and `scale_overrides.json`, which are the two files you are meant to hand edit.

In **your Godot project** the opposite applies. Commit `materials/` there: it is small, and the `.tres` are where you would tune things like `texture_filter` for atlas bleed. Rerunning the generator overwrites them, so those edits are lost; reconverting models does not touch them.

For how the conversion works and why it makes the choices it does, see [docs/DESIGN.md](docs/DESIGN.md).
