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
foliage_overrides.json textures for foliage whose FBX names none at all
material_overrides.json curated flavor sets and material bindings

tools/                 Godot side scripts, copied into your project as <project>/tools/
synty_packs_fbx/       put the packs here
assets/                output: converted packs         -> <project>/assets/
materials/             output: material manifests      -> <project>/materials/
```

## What it fixes

Synty FBX do not import cleanly into Godot on their own. The converter deals with five problems:

- **Scale and axes.** The packs are authored in Maya in centimeters, Y-up. A naive conversion gives you a `Node3D` or `Skeleton3D` scaled to 1/100 and rotated 90 degrees, which throws off every `BoneAttachment3D`, collision shape and root motion value. The converter bakes that away, so a character arrives 1.79 m tall, upright, standing at Y = 0, on identity transforms. A few packs declare a unit their geometry is not actually in; see [Fixing a pack that converts too small](#fixing-a-pack-that-converts-too-small).
- **Broken texture references.** Every material points at internal authoring files that were never shipped, usually named for a different pack. The converter works out which shipped texture each one meant.
- **Dropped material channels.** A handful of Synty materials carry an emissive map, a normal map or a transparency mask alongside their atlas. The converter carries every channel the FBX declares across to the GLB; see [What the materials carry](#what-the-materials-carry).
- **Duplicated textures.** Synty FBX embed their atlas, so a naive conversion copies a 2048x2048 PNG into every model. The converter references one shared file instead.
- **Authoring leftovers.** Some models carry a single-key `Take 001` that only restates the import transform, or an Unreal `UCX_` collision hull. Both are dropped. Left in, the take blocks normalization and then reapplies in Godot the very transform normalization exists to remove, and the hull arrives as a visible untextured box over the prop it was meant to bound.
- **Stacked LOD levels.** The Nature Biomes packs ship each tree and bush as a chain of progressively cheaper meshes, which Godot has no way to read as a chain and therefore renders all at once. Only the finest level is kept; see [LOD chains](#lod-chains).
- **Card foliage that names no texture.** The same packs export their detailed trees and bushes with the material bindings stripped, leaving one grey Lambert over trunk and canopy alike. Since Synty foliage is alpha cards, that arrives as a white tree with solid quads where the leaves should be. The converter separates the two and binds both; see [Foliage that names no texture](#foliage-that-names-no-texture).
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

## What the materials carry

Every channel an FBX declares is carried across. The converter reads each one from the Principled BSDF socket Blender's FBX importer drove, so a material in the GLB says what the source material said, and the generated `.tres` says the same thing again.

| FBX property | Becomes | Declared across 10,488 source materials |
| --- | --- | --- |
| `DiffuseColor` | base colour texture, or the colour where no map is bound | 9,943 |
| `TransparentColor` | alpha, cut out at 0.5 | 179 |
| `EmissiveColor` | emissive texture, or emissive colour | 79 map, 4 colour |
| `NormalMap` / `Bump` | normal map, with tangents exported for those models | 125 |
| `Shininess` | roughness, as `1 - sqrt(shininess) / 10` | 753 |
| `ReflectionFactor` | metallic | 710 |

Two things are worth knowing before you go looking for glowing props. **Only two packs wire up emission at all**: NatureBiomes AridDesert on 77 materials, and SciFiSpace on 2. **The models named for a glow do not have any.** `SM_Env_GlowingOrb_01`, `SM_Veh_WarpGate_Glow_01`, the Dungeons Realms obelisks and the Military glowstick each declare a single diffuse texture and nothing else. Several packs ship an `Emissive_0x.png` atlas that no FBX in the pack references. That glow lived in Unity materials, which are not part of the source packs, so there is nothing in the FBX to recover.

Normal maps are similar. Of 125 bindings, 87 name no file at all (the path was emptied before export) and most of the rest name a file the pack never shipped, which leaves 5 that resolve. They are reported like any other unresolved reference.

Materials are still keyed on their atlas, but a material that carries anything beyond one takes a qualifier so it cannot collapse into the plain material wearing the same atlas and quietly lose a map:

```
PolygonNatureBiomesS2_AridDesert_Texture_01                      the atlas alone
PolygonNatureBiomesS2_AridDesert_Texture_01_Emissive_01_A        the same atlas, plus an emissive map
PolygonNatureBiomesS2_AridDesert_Texture_01_Emissive_01_A_Cutout the same again, masked
Lambert_A45_808080                                               no atlas: alpha 0.45, grey
```

Qualifiers come from the material itself and never from the order files happen to be converted in, so one name means one thing across a whole pack.

Two limits are worth stating. glTF carries coverage on the base colour texture's alpha channel rather than in a map of its own, so a mask has to be the file the material is coloured with. Every Synty material that binds a mask already names the same file, apart from eleven Military fences that name only the mask, which then supplies their colour too. And a mask whose file has no alpha channel cannot be expressed at all; those are warned about and left opaque.

Godot imports a normal map correctly on its own, but check the texture's **Import** dock shows **Normal Map** under compression if a surface looks flat.

## LOD chains

Three of Synty's Nature Biomes packs ship each tree and bush as a chain of progressively cheaper meshes, sitting side by side under one root, with a model's trunk and its canopy each carrying a chain of their own:

```
SM_Env_Tree_Meadow_01
  SM_Env_Tree_Meadow_01_Branches_LOD0     3,116 tris
  SM_Env_Tree_Meadow_01_Branches_LOD1
  SM_Env_Tree_Meadow_01_Branches_LOD2
  SM_Env_Tree_Meadow_01_LOD0             22,162 tris
  SM_Env_Tree_Meadow_01_LOD1
  SM_Env_Tree_Meadow_01_LOD2
  SM_Env_Tree_Meadow_01_LOD3                 12 tris, a flat billboard imposter
```

Nothing downstream reads that as a chain. Unity and Unreal understand an FBX LOD group; Godot has no such concept, takes no meaning from the naming, and does not implement the `MSFT_lod` glTF extension. Every level therefore arrives as an ordinary `MeshInstance3D` and all of them render together, so the tree costs several times what it should and the imposter card stands crossed through the middle of the tree it exists to stand in for. Godot's other LOD mechanism, `visibility_range_begin` and `visibility_range_end`, is a property of the node and cannot be carried in a GLB at all.

So the converter keeps the finest level of each chain and drops the rest, which is what Godot wants anyway: its glTF importer has LOD generation on by default and builds a chain out of whatever it is handed. Across the three packs that is 255 meshes dropped from 132 models.

```
LODs      dropped 255 coarse level(s) from 132 model(s)
```

`--lods keep` ships the whole chain instead. It is worth taking if you intend to wire the visibility ranges up by hand, because meshoptimizer's simplification is weaker than an artist's LODs on alpha cutout foliage and the billboard is cheaper at distance than anything generated from the full mesh.

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

The file ships with 48 mappings covering 14 packs, since these are facts about Synty's packs rather than anything project specific. They are the cases where a shipped texture is the unique, obvious counterpart, for example `PolygonScifi_Texture.psd` meaning `PolygonScifi_01_A.png`, or an artist's working copy like `PolygonWesternFrontier_Texture_Mike.psd`. A working file's name is not evidence of what it holds: `RopeBridge.png` is the atlas for 45 Meadow Forest props, none of which is a rope bridge, because the artist named the file in the Tropical Jungle scene that does have one. If you own a pack that is not listed, run `--scan-materials` and add what you find.

What is deliberately **not** mapped here is anything ambiguous between several shipped candidates: guessing would put a plausible but wrong texture on the model, which is harder to notice than an obviously untextured one. References to packs you do not own, like `PolygonAncientWorlds_Texture_01.png`, have no counterpart to find and stay colour-only regardless. `Wall_01.psd` in FantasyKingdom used to be the example of the first kind, since it could be any of five shipped wall textures; it is mapped now, to `Wall_Brick_01.png`, because that stopped being a guess once `Wall_Brick_01` became the curated default of FantasyKingdom's `Wall` flavor set, the same texture a bare wall material falls back to on its own. See [Flavor sets and default textures](#flavor-sets-and-default-textures) for how that default is chosen, and for the general case of a material naming no texture at all rather than an ambiguous one.

### Materials that name no texture at all

An unresolved reference is one the matcher could not place. A material can also arrive naming nothing whatever, which the scan counts separately, as `untextured`. `texture_overrides.json` cannot reach those, since it is keyed on the texture stem the FBX asks for and there is no stem to key on.

Most are untextured because they were never meant to be textured: glass, water planes, fog rings, sky domes. Foliage is the exception, and it has a file of its own; see below.

Some, though, are whole models that arrive as a flat white or grey blob. Fantasy Kingdom was the worst case: 15 of its 26 `SM_Bld_Preset_*_Optimized` buildings declare exactly one material, usually named `PolygonCastle_GLASS`, because the body material was stripped when Synty exported them; the other 11 either kept both materials or named a single texture that still resolves on its own, and always converted correctly. The FBX itself says nothing about the missing atlas, but the mesh does: every stripped building's UVs span the same 0 to 1 island as the hundreds of models that resolve onto `PolygonFantasyKingdom_01_A` correctly, which is what identifies it. `material_overrides.json` now binds that material to the atlas on every `SM_Bld_Preset_*` model, so these 15 convert correctly too; see [Flavor sets and default textures](#flavor-sets-and-default-textures).

### Filling, keeping or dropping untextured models

`--untextured MODE` decides what happens to a material that bound no texture on any channel. The default is `fill`, which gives it the default texture from its flavor set in `material_overrides.json`, which is what most of Synty's bare Maya placeholders want; see [Flavor sets and default textures](#flavor-sets-and-default-textures). The other three modes turn that off, drop what is still bare after it runs, or both:

| Mode | Fills a bare material from its flavor set | Drops what is still bare |
| --- | --- | --- |
| `fill` (default) | yes | no |
| `keep` | no | no |
| `drop` | no | yes |
| `fill-or-drop` | yes | yes |

`keep` is the old, entirely hands-off behaviour, and is also how you audit a pack: `--untextured keep --scan-materials` shows every material exactly as bare as its FBX left it, with no fill in the way. `drop` writes no GLB for a model whose materials bound no texture on any channel, without filling first, and deletes one an earlier run left behind. `fill-or-drop` does both, filling whatever a binding covers and dropping whatever is still bare afterwards:

```bash
python synty_convert.py --untextured fill-or-drop --force
```

Three things drop and fill-or-drop deliberately do not catch:

- **Animation files.** They carry a skeleton and no mesh, so there is nothing to texture. Geometry is the qualifier, which is what keeps the 694 files of the Base Locomotion pack out of it.
- **Foliage bound from `foliage_overrides.json`.** Those bindings are applied before materials are resolved, so a tree whose FBX named nothing has real textures by the time the decision is made.
- **A model that is only partly untextured.** One textured material is enough to keep it, since the rest may be glass or water that was never meant to carry one.

What drop and fill-or-drop do catch, which is worth knowing before you use them, is any untextured model that is not a blob: `PolygonSyntyCharacter.fbx` and `SidekickSyntyCharacter.fbx`, the two reference bodies the Base Locomotion animations are built against, bind no texture, have no flavor set to fall back to either since Base Locomotion declares none, and so are dropped along with the rest. Convert that pack with `--untextured keep` if you want them.

Every mode except `keep` needs materials to judge, so it cannot be combined with `--materials none`. It also only sees models that actually go through Blender: a model whose GLB is already up to date is never re-examined, so switching modes on an existing conversion wants `--force`. The summary says how many were left out, per pack:

```
Untextured 187 model(s) not written, no material bound a texture
               2  ANIMATION_Base_Locomotion_SourceFiles_v3
               9  POLYGON_BattleRoyale_Source_Files_v4
              47  POLYGON_Construction_SourceFiles_v3
               8  POLYGON_Dungeons_Realms_SourceFiles_v2
              18  POLYGON_Military_SourceFiles_v4
               7  POLYGON_NatureBiomes_AridDesert_SourceFiles_v2
               8  POLYGON_NatureBiomes_MeadowForest_SourceFiles_v2
              22  POLYGON_NatureBiomes_TropicalJungle_SourceFiles_v2
               4  POLYGON_Nature_Source_Files_v2
               7  POLYGON_SciFi_City_SourceFiles_v5
               5  POLYGON_Western_Frontier_SourceFiles_v4
              31  PolygonFantasyKingdom
              19  PolygonSciFiSpace
```

That is `--untextured fill-or-drop`, 187 of 10740 models, or 1.7%, after filling has already found a home for most of them. `--untextured drop` alone, with no filling, would remove 587 of the same 10740, or 5.5%: the gap between the two is exactly what flavor sets buy you.

## Flavor sets and default textures

Synty ships several real textures for a surface whose choice belongs to the consumer, not the pack: FantasyKingdom alone ships five tileable castle walls and eight roof surfaces, and its `Textures/Alts` folder, Synty's own name for a recolour, carries three palettes of the pack's main atlas. Nothing in an FBX says which one a model should wear, because the choice is meant to be made in the game, not baked into the export. `--untextured fill`, the default, makes that choice for you by filling a bare material with a curated default, and `material_overrides.json` is where that default, and the rest of its set, is declared.

Two genuinely different things share this mechanism. A **tileable surface** like `Wall` is five unrelated textures that happen to serve the same role: a bare wall material can wear any one of them and look equally correct. A **colourway alt** like FantasyKingdom's `Atlas` set is Synty's own concept, one atlas repainted in a few palettes; a model wearing it is not choosing a different surface, only a different coat of paint on the same one. Both are real, curated flavor sets, and both work the same way once declared: every member ships as a material regardless of which one a bare material defaults to, which is what makes swapping possible later. Point a model's surface material at a different flavor's `.tres` in Godot and the model wears that flavor instead, because every alternative is already sitting in `materials/<pack>/materials.json`, not generated on demand.

**Why curated rather than detected.** Grouping textures by name looks tempting and is wrong more often than it is right. `Bullet_Decal_Metal_01_D` and `Bullet_Decal_Metal_01_N` in the Military pack share every token but one, and a lexical clustering would offer the normal map as a flavor of the diffuse it is actually meant to sit beside, not swap with. NatureBiomes' `WaterNormals_01` and `WaterNormals_02` cluster the same way despite being two maps of one material, not two choices for one. And a pack's `Textures/LOD_Cards` folder, which carries a `treeBirch_01.tga` through `treeBirch_04.tga` in MeadowForest, is a bake per LOD level of one specific tree, not four trees to choose between. `material_overrides.json` exists for the same reason `texture_overrides.json` does: matching by name finds real patterns and wrong ones at the same rate, and a wrong flavor is exactly the plausible-but-incorrect result this tool is built to avoid.

**Schema.** Each pack entry declares `flavors`, named sets of interchangeable textures, and `bind`, an ordered list saying which materials fall back to which set. Trimmed from FantasyKingdom's real entry:

```json
{
  "PolygonFantasyKingdom": {
    "flavors": {
      "Wall": {
        "members": ["Textures/Castle/Wall_*.png"],
        "default": "Wall_Brick_01.png"
      },
      "Atlas": {
        "members": ["Textures/Alts/PolygonFantasyKingdom_01_*.png"],
        "default": "PolygonFantasyKingdom_01_A.png"
      }
    },
    "bind": [
      { "model": "SM_Bld_Preset_*", "material": "PolygonCastle_GLASS", "flavor": "Atlas" },
      { "material": "Wall*", "flavor": "Wall" },
      { "model": "SM_Bld_Preset_*_Optimized", "material": "*", "flavor": "Atlas" }
    ]
  }
}
```

A flavor's `members` are path-suffix globs, matched against the pack's shipped textures the same way `texture_overrides.json` matches an override, because a shipped file is found by the tail of its path. `default` names which member a bare material actually gets; a set whose default does not match exactly one of its own members is dropped with a warning rather than guessed at, since the default is the one texture applied without anyone asking for it by name. A set can also declare `"cutout": true`, for a flavor whose members are Synty foliage or netting cards rather than opaque surfaces: such a card has no coverage of its own to cut with until the same image is bound as both colour and mask, so the flag tells the fill to bind it as both rather than colour alone, which would ship the leaf as a solid rectangle.

A `bind` entry's `material` and, optionally, `model` are glob patterns too, but anchored ones matched against a whole name rather than a path suffix, because a material name or a filename stem is a whole name and not the tail of a path. `model` defaults to `*` when left out, which is most bindings: `Wall*` needs no model scoping, because every material named that way wants the `Wall` flavor regardless of which model wears it.

**Order matters, and `model` scoping is why.** `bind` is an ordered list, and the first entry whose `model` and `material` both match wins, so a narrow model-scoped rule can sit above a broader one without the broader one undoing it. FantasyKingdom's `PolygonCastle_GLASS` is why the option exists: the same bare name means real glass on the preset windmill, and means an entire building body stripped of its real material on the other 15 `SM_Bld_Preset_*_Optimized` presets (see [Materials that name no texture at all](#materials-that-name-no-texture-at-all)), and the UV evidence for filling it with `Atlas` was only ever gathered for that family. Scoping the rule to `model: SM_Bld_Preset_*` keeps the fix inside the family the evidence covers, rather than asserting pack-wide that a material named `PolygonCastle_GLASS` always means the castle atlas; the accepted cost is that the windmill's own glass is filled the same way as the fifteen broken bodies, since nothing in a bare material's name distinguishes the two.

**Filling renames the material after its new texture**, the same way any resolved reference does, so it merges with any material that already wears that texture correctly. Before this feature, `SM_Bld_Castle_Wall_Window_Big_01.glb` carried a correctly textured atlas material alongside a second material named `Wall`, bare, because Synty's export left its wall body pointing at nothing. Filled, that material is renamed `Wall_Brick_01` and merges into the same manifest entry as every other material that already resolved to `Wall_Brick_01.png` on its own; the bare `Wall` disappears from the manifest and `Wall_Brick_01`'s usage count grows instead. `SM_Bld_Castle_Wall_Window_Big_01.glb` now carries two textured materials, `PolygonFantasyKingdom_01_A_A92_R75_M50` and `Wall_Brick_01`.

**Every set member ships regardless of mode.** FantasyKingdom's manifest carries all five wall flavors, each in a plain and an `_R69_M50` qualified form: `Wall_Brick_01`, `Wall_Dungeon_01`, `Wall_Evil_01`, `Wall_Large_Brick_01` and `Wall_Stucco_01`. Eight of those ten materials are used by zero models; they exist purely so a Godot project can point a model's surface at a different one, which is the entire point of shipping a curated set rather than only the one default. Authoring one is what `--untextured keep` and `--scan-report` are for together: `keep` turns filling off so a scan shows every material exactly as bare as its FBX left it, and `--scan-report PATH` dumps that as JSON per model rather than aggregated per pack, which is what a `model`-scoped `bind` entry has to be written from rather than guessed at:

```bash
python synty_convert.py --scan-materials --untextured keep --scan-report scan.json --packs PolygonFantasyKingdom
```

**Three warnings guard the table against rotting** as a pack is updated. A `bind` entry that never matched a model and material together is reported as `DEAD`, naming the rule so it can be fixed or removed rather than sitting there doing nothing:

```
DEAD     binding 'lambert260' on model 'SM_Bld_Lift_01' matched nothing; remove it from material_overrides.json or fix its glob
```

A flavor's `members` glob that matches no shipped texture is reported too, which is what catches a texture Synty renamed or removed between pack versions. And a `default` that does not match exactly one of its own set's members drops the whole set with a warning, rather than leaving every material bound to it silently uncorrected. All three surface in the same per-pack summary a plain conversion already prints, so a stale binding is a warning to read rather than a bug to find later.

## Foliage that names no texture

Synty's Nature Biomes packs export their detailed trees and bushes with the material bindings stripped. One grey Lambert covers the whole model, and because that foliage is built from alpha cutout cards, each quad drawing a leaf texture across the whole of UV space, an untextured card has no coverage to cut with. The model arrives white with solid quads where its leaves should be, which is a good deal more broken looking than merely grey.

The two halves cannot share a material. A leaf card spans the whole of UV space and the trunk's own UVs sit underneath, so no single image can serve both. They separate cleanly by geometry though: a leaf card is one quad, while a trunk is a single island of hundreds of triangles. The converter splits on island size and binds each half from `foliage_overrides.json`:

```json
{
  "POLYGON_NatureBiomes_MeadowForest_SourceFiles_v2": {
    "SM_Env_Tree_Birch_*": {
      "trunk": "Textures/Plants/Birch_Trunk_Texture.png",
      "canopy": "Textures/Plants/leafPatch_01.tga",
      "branches": "Textures/Plants/Branches_02.tga"
    }
  }
}
```

`canopy` is the leaf cards, bound as a cutout. `trunk` is the woody geometry they hang off, bound opaque. `branches` is a separate twig mesh, where a model has one. Values are path suffixes matched against the pack's shipped textures, exactly as in `texture_overrides.json`, and the first matching glob wins, so put the specific patterns first. A model that names only a `canopy` is not split at all, which is what the pure card bushes want.

```
Foliage   bound 39 mesh(es) across 25 model(s) whose FBX named no texture
```

The file ships mappings for 23 models across MeadowForest and TropicalJungle: the birch, fruit and meadow trees, the forest and pohutukawa trees, and the bushes that go with them.

**How the mappings were arrived at**, since guessing a texture is otherwise exactly what this tool refuses to do. Each pack ships a `Textures/LOD_Cards` image per tree, which is a baked render of the finished model and therefore a picture of the answer. Every candidate leaf map was scored against its card's palette, and the method reproduces the two mappings that were already known independently: `leafPatch_01` for the birches, and `pohutukawaLeaf` for the pohutukawas, which is also an exact name match. Trunks were confirmed by sampling each candidate at the trunk's own UV coordinates: on these models a trunk collapses to a single point of the pack atlas, holding the bark colour it was authored with. The birches are the exception, their trunks spanning a whole dedicated bark map.

Two things are deliberately left alone. **Palms, bananas, cacti and succulents** in these packs are not card foliage at all but solid geometry wearing a dedicated full-UV texture, so they are a different problem and stay untextured. And in the base **Nature** pack, `Trunk_FF0000` and `Leave_34FF00` are already separate materials on separate surfaces, so those models need no splitting and are better fixed by hand in Godot, or by keying an override on the material name, which this file does not yet do.

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
| `--split-heads`     | off                | Put each character's head on its own mesh node                |
| `--untextured MODE` | `fill`             | `fill` fills a bare material from its flavor set default, `keep` leaves it flat colour, `drop` writes no model for it, `fill-or-drop` does both |
| `--scan-materials`  | off                | Report texture resolution only, write nothing                 |
| `--scan-report PATH`| off                | With `--scan-materials`, dump every model's raw material records to `PATH` |
| `--force`           | off                | Reconvert files that are already up to date                   |
| `--verify`         | off                | Reimport each GLB and check bounds, bone and action parity    |
| `--vertex-colors`  | `drop`             | `keep` retains colour attributes                              |
| `--animations`     | `keep`             | `drop` discards baked-in takes                                |
| `--lods`           | `drop`             | `keep` ships every LOD level, which renders them all at once  |
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

**A tree or bush is white, with flat quads where its leaves should be**
Its FBX binds no texture, so the alpha cutout that shapes each leaf card has nothing to cut with. Add it to [foliage_overrides.json](#foliage-that-names-no-texture) and reconvert with `--force`. Quads spanning the whole canopy are a separate thing, the LOD imposter, and mean the model was converted with `--lods keep`.

**A building or prop is flat white or flat grey, with no texture anywhere on it**
Its FBX bound no texture to begin with, so there was nothing to carry across, and by default the converter already tries to fix it. Check the model's materials in `materials/<pack>/materials.json`: an entry with `albedo_color` and no `albedo_texture` still has no flavor set covering it. Where the missing atlas is a single obvious texture reference, name it in [texture_overrides.json](#fixing-unresolved-textures); where the choice is between several shipped alternatives, or the material references no file at all, add a set and a binding to [material_overrides.json](#flavor-sets-and-default-textures) instead. To leave what is still bare out of the output rather than fix it, convert with [`--untextured drop` or `fill-or-drop`](#filling-keeping-or-dropping-untextured-models).

**Godot is importing my source FBX**
Keep `synty_packs_fbx/` outside your Godot project, or drop an empty `.gdignore` file in it.

## Notes

Nothing this repo generates is committed. `assets/` and `materials/` are gitignored, since both are reproducible by rerunning the converter, and both are created on demand. The packs are ignored too: `synty_packs_fbx/` keeps only a `.gitkeep`, so the folder is there to drop packs into without any of them being committed. What is tracked is the tool itself, plus the curated override files you are meant to hand edit: `texture_overrides.json`, `scale_overrides.json`, `foliage_overrides.json` and `material_overrides.json`.

In **your Godot project** the opposite applies. Commit `materials/` there: it is small, and the `.tres` are where you would tune things like `texture_filter` for atlas bleed. Rerunning the generator overwrites them, so those edits are lost; reconverting models does not touch them.

For how the conversion works and why it makes the choices it does, see [docs/DESIGN.md](docs/DESIGN.md).
