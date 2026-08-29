# Materials

How a Synty material becomes a Godot one, and the three curated files that handle what a matcher cannot work out on its own.

- [What the materials carry](#what-the-materials-carry)
- [Reading the scan report](#reading-the-scan-report)
- [texture_overrides.json: references that will not resolve](#texture_overridesjson-references-that-will-not-resolve)
- [material_overrides.json: flavor sets](#material_overridesjson-flavor-sets)
- [material_overrides.json: companion maps](#material_overridesjson-companion-maps)
- [Filling, keeping or dropping untextured models](#filling-keeping-or-dropping-untextured-models)

## What the materials carry

Every channel an FBX declares is carried across. The converter reads each one from the Principled BSDF socket Blender's FBX importer drove, so a material in the GLB says what the source material said, and the generated `.tres` says the same thing again.

| FBX property | Becomes |
| --- | --- |
| `DiffuseColor` | base color texture, or the color where no map is bound |
| `TransparentColor` | alpha, cut out at 0.5 |
| `EmissiveColor` | emissive texture, or emissive color |
| `NormalMap` / `Bump` | normal map, with tangents exported for those models |
| `Shininess` | roughness, as `1 - sqrt(shininess) / 10` |
| `ReflectionFactor` | metallic |

Two things are worth knowing before you go looking for glowing props.

**The FBX themselves are nearly silent about emission.** Across 22 packs, 182 materials name an emissive file and four more declare an emissive color with no map. The models named for a glow declare none of their own: `SM_Env_GlowingOrb_01`, `SM_Veh_WarpGate_Glow_01` and the Dungeons Realms obelisks each carry a single diffuse texture and nothing else, because the glow lived in Unity materials that are not part of the source packs. What did ship is the map itself, and [companion maps](#material_overridesjson-companion-maps) are where those get wired back up.

**Normal maps are the same story.** 82 materials name a normal file and only 5 of them resolve; the rest name files their packs never shipped. Companions supply 95 more bindings across 14 packs, which is where all but a handful of the normal maps in the output come from.

### How materials are named

Materials are keyed on the resolved texture rather than the FBX material name, because Synty's names are Maya leftovers that are ambiguous across files: in BattleRoyale, `lambert1` alone maps to four different textures. Untextured materials key on color and alpha instead, so `glass`, `glass1` and `glass2` collapse into one.

A material that carries anything beyond an atlas takes a qualifier, so it cannot collapse into the plain material wearing the same atlas and quietly lose it:

```
PolygonNatureBiomesS2_AridDesert_Texture_01        the atlas, plus the emissive declared for it
PolygonNatureBiomesS2_AridDesert_Texture_01_Cutout the same again, masked
PolygonNatureBiomesS2_AridDesert_Texture_01_A45    the same again at alpha 0.45
Lavawave_Hot                                       a different sheet, carrying both companion channels
Lambert_808080                                     no atlas: gray
```

A companion map is the one thing that never contributes a qualifier. It is declared per atlas, so every material wearing that atlas gets the same map and naming it would only repeat the atlas name. This follows the packs' own convention: Synty ships `Wall_Brick_01.png` beside `Wall_Brick_01_Normals.png`, so the base is the material and the suffix marks a channel.

A map that is *not* the one its atlas declares still qualifies, because that is the case where two materials on one atlas can differ.

### Two limits

glTF carries coverage on the base color texture's alpha channel rather than in a map of its own, so a mask has to be the file the material is colored with. Every Synty material that binds a mask already names the same file, apart from eleven Military fences that name only the mask, which then supplies their color too. A mask whose file has no alpha channel cannot be expressed at all; those are warned about and left opaque.

Godot imports a normal map correctly on its own, but check the texture's **Import** dock shows **Normal Map** under compression if a surface looks flat.

## Reading the scan report

`--scan-materials` reads the FBX and reports what each material would resolve to, without writing anything. Run it on a pack you have not converted before.

```bash
python synty_convert.py --scan-materials --packs PolygonFantasyKingdom
```

```
PolygonFantasyKingdom: 20 materials  (0 exact, 3 override, 8 heuristic, 6 unresolved, 16 filled, 3 untextured)
   8 carry an emission map, 7 carry a normal map
   companion PolygonFantasyKingdom_01_A.png   -> emission PolygonFantasyKingdom_01_Emmisive.png  (1978 models)
   candidates 6 unbound companion map(s): Grass_02_Normals.png, Grass_03_Normals.png, ...
   review  PolygonCastle_Texture_01_A.psd -> PolygonFantasyKingdom_01_A (1893 files)
   manual  Paintings_02.psd -> Paintings_01 (18 files)
   UNRESOLVED  PolygonCastle_Texture_Normal_01.png  (19 files, no normal map; add to texture_overrides.json)
```

| Line | Meaning |
| --- | --- |
| `review` | Resolved by heuristic. Worth a glance. |
| `manual` | Came from `texture_overrides.json`. |
| `UNRESOLVED` | No confident match, so that channel binds nothing. |
| `filled` | A bare material took its [flavor set](#material_overridesjson-flavor-sets) default. |
| `flavor` | A declared flavor set and how many textures it covers. |
| `companion` | A [companion map](#material_overridesjson-companion-maps) that reached materials models actually wear. |
| `sibling` | A companion nothing wears today, but that a generated flavor sibling will. |
| `candidates` | The authoring worklist: maps the pack ships that nothing has been told to use. |
| `DEAD` | A binding or companion that matched nothing. Fix it or remove it. |

`DEAD` is an absence claim, so only a run that could have watched a binding fire prints one. `--untextured keep` and `drop` never hand the worker a binding table, and an incremental run only examined the models it reconverted; those runs say `health not assessed` instead.

Add `--scan-report PATH` to dump every model's raw material records as JSON. That per-model view, not the aggregated one, is what a model-scoped binding has to be written from.

## texture_overrides.json: references that will not resolve

Synty's FBX reference authoring files that never shipped, so some references cannot be matched. When that happens the material keeps its color and is reported.

Add the mapping keyed by pack folder name, then by the texture stem the FBX asks for. Values are path suffixes matched against the pack's shipped textures:

```json
{
  "POLYGON_BattleRoyale_Source_Files_v4": {
    "Air_Vehicle_Master_01": "Textures/PolygonBattleRoyale_Plane_01.png",
    "track2": "Textures/PolygonBattleRoyale_Tank_Tracks.png"
  }
}
```

Overrides beat the heuristic, so use them to correct a wrong match too. Then rerun with `--force` and regenerate the materials.

Sometimes the texture a pack asks for is one **another pack** ships. Synty's biome packs are built on the base Nature pack and reference its atlas directly. Name the other pack ahead of the suffix:

```json
{
  "POLYGON_NatureBiomes_MeadowForest_SourceFiles_v2": {
    "PolygonNature": "POLYGON_Nature_Source_Files_v2::Textures/PolygonNature_01.png"
  }
}
```

That other pack has to be converted too, since the material points at its mirrored copy under `assets/`. Convert only one of the pair and the run says so.

The file ships with 83 mappings covering 19 packs, since these are facts about Synty's packs rather than anything project specific. They are the cases where a shipped texture is the unique, obvious counterpart, for example `PolygonScifi_Texture.psd` meaning `PolygonScifi_01_A.png`. A working file's name is not evidence of what it holds: `RopeBridge.png` is the atlas for 45 Meadow Forest props, none of which is a rope bridge, because the artist named the file in the Tropical Jungle scene that does have one.

What is deliberately **not** mapped is anything ambiguous between several shipped candidates. A wrong texture renders as plausible but incorrect art, which is far harder to notice than an obviously untextured model. Where a pack ships several equally plausible candidates for one surface, use a flavor set instead. References to packs you do not own have no counterpart to find and stay color-only.

### Materials that name no texture at all

An unresolved reference is one the matcher could not place. A material can also arrive naming nothing whatever, which the scan counts separately as `untextured`. `texture_overrides.json` cannot reach those, since it is keyed on the texture stem the FBX asks for.

Most are untextured because they were never meant to be textured: glass, water planes, fog rings, sky domes. Some, though, are whole models that arrive as a flat white blob. Fantasy Kingdom was the worst case: 15 of its 26 `SM_Bld_Preset_*_Optimized` buildings declare exactly one material, usually named `PolygonCastle_GLASS`, because the body material was stripped on export. The FBX says nothing about the missing atlas, but the mesh does, since every stripped building's UVs span the same island as the hundreds of models that resolve correctly. Those are handled by flavor sets, below.

## material_overrides.json: flavor sets

Synty ships several real textures for a surface whose choice belongs to the consumer, not the pack: FantasyKingdom alone ships five tileable castle walls and eight roof surfaces, and its `Textures/Alts` folder carries three palettes of the pack's main atlas. Nothing in an FBX says which one a model should wear, because the choice is meant to be made in the game.

`--untextured fill`, the default, makes that choice by filling a bare material with a curated default. Every other member of the set still ships as a material, so a model can be re-skinned in Godot by pointing it at a different generated `.tres`.

Two genuinely different things share this mechanism. A **tileable surface** like `Wall` is five unrelated textures that happen to serve the same role. A **colorway alt** is Synty's own concept, one atlas repainted in a few palettes. Both work the same way once declared.

### Schema

Each pack entry declares `flavors`, named sets of interchangeable textures, and `bind`, an ordered list saying which materials fall back to which set:

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

| Key | Matching |
| --- | --- |
| `flavors.*.members` | Path-suffix globs against the pack's shipped textures, as in `texture_overrides.json` |
| `flavors.*.default` | Must match exactly one of the set's own members, or the set is dropped with a warning |
| `flavors.*.cutout` | Optional. Binds the member as both color and mask, for alpha cards |
| `bind[].material` | Whole-name glob against the FBX material name, anchored at the start |
| `bind[].model` | Whole-name glob against the model filename stem. Defaults to `*` |

**Order matters, and `model` scoping is why.** The first entry whose `model` and `material` both match wins, so a narrow model-scoped rule can sit above a broader one without the broader one undoing it. FantasyKingdom's `PolygonCastle_GLASS` is why the option exists: the same bare name means real glass on one preset and an entire stripped building body on fifteen others, and the UV evidence for filling it was only gathered for that family.

**Filling renames the material after its new texture**, the same way any resolved reference does, so it merges with any material that already wears that texture correctly. A bare `Wall` becomes `Wall_Brick_01` and joins the manifest entry every other model already resolved to.

**Every set member ships regardless of mode.** FantasyKingdom's manifest carries all five wall flavors, most of them used by zero models. They exist so a Godot project can swap to one, which is the entire point of shipping a curated set rather than only the default.

### Why curated rather than detected

Grouping textures by name looks tempting and is wrong more often than it is right.

- `Bullet_Decal_Metal_01_D` and `_01_N` share every token but one, and a lexical clustering would offer the normal map as a flavor of the diffuse it is meant to sit beside.
- `WaterNormals_01` and `WaterNormals_02` cluster the same way despite being two maps of one material.
- A pack's `Textures/LOD_Cards` folder carries `treeBirch_01.tga` through `_04.tga`, which is a bake per LOD level of one tree, not four trees to choose between.

Matching by name finds real patterns and wrong ones at the same rate.

### Three warnings guard the table against rotting

- A `bind` entry that never matched a model and material together is reported as `DEAD`.
- A `members` glob that matches no shipped texture is reported, which catches a texture Synty renamed between pack versions.
- A `default` that does not match exactly one of its set's members drops the whole set with a warning.

To author a set for a new pack, run a scan with filling turned off so you can see what is actually bare:

```bash
python synty_convert.py --scan-materials --untextured keep --scan-report scan.json --packs PolygonFantasyKingdom
```

That run reports no `DEAD` lines, since `keep` hands the worker no binding table. Judge binding health with a separate `--untextured fill --scan-materials` pass.

## material_overrides.json: companion maps

Synty packs ship emissive and normal maps that no FBX references. The wiring for them lived in Unity materials, which are not part of the source drop, so the file arrives in the pack's texture folder with nothing pointing at it and no symptom other than a prop that never lights up.

The `companions` table says which map belongs with which atlas, and the converter fills any material channel that resolved to nothing:

```json
{
  "POLYGON_SciFi_City_SourceFiles_v5": {
    "companions": {
      "Textures/PolygonScifi_01_A.png": {
        "emission": "Textures/PolygonScifi_Emissive_01.png",
        "normal": "Textures/PolygonSciFiCity_Texture_Normal.png"
      },
      "Textures/PolygonSciFi_Road_01.png": {
        "normal": "Textures/PolygonSciFi_Road_Normal.png"
      }
    }
  }
}
```

**Keyed on the atlas rather than on a material**, because that is what a companion belongs to: one emissive serves every material wearing the sheet it was painted for, and usually every recolor of it too. So a key is a path-suffix glob that may match many shipped textures, while each channel's **value must match exactly one file**. The asymmetry is the point: a key that matches too widely costs one wasted entry, whereas a value matching two candidates would put a texture nobody chose onto every material wearing that atlas. A value matching zero or more than one file is dropped with a warning, and two keys overlapping on the same atlas and channel are reported rather than resolved by whichever sorted first.

Keying on the atlas also means a companion follows a material however it reached its texture, including one a flavor fill has just supplied.

**A companion only ever fills a channel that resolved to nothing**, so a map the FBX named itself always wins. **A companion never renames the material**, since it is implied by the atlas.

**There is no flag.** Companions are curated facts about a pack, like the other override files, so they apply under every `--untextured` mode. Under `keep` they reach fewer materials, because there are no flavor-filled albedos left for them to key off; that is a consequence of `keep` filling nothing, not a shortfall. Judge whether a companion table is dead with `--untextured fill`.

### Why every binding is recorded with its evidence

A wrong pairing is silent. It ships a real map on a real atlas and the model renders, just wrong, which is far harder to notice than a missing one. So each pack's entry carries a `_verification` block saying what was measured. The filenames are wrong often enough that reading a pairing off them would have gone wrong in at least four packs:

- **Casino.** `HotelWall_01_normals` fits `HotelWall_01` through `_04` and `HotelWall_02_normals` fits `_05` through `_07`, which correlating each map's relief against each sheet's edges separates by two orders of magnitude. `HotelWall_03_normals` onward are not walls at all.
- **FantasyKingdom.** The two atlas normals are named backwards. `_01_Normals` serves families 01 to 03 and `_02_Normals` serves family 04, which the numbering suggests the other way round.
- **Street Racer.** The four `..._Emissive_0N` sheets are not one emissive per numbered atlas family. They are four neon colorways of the same swatch strip, and only `_01` lands on the sheets as they shipped.
- **Nature.** `emissive.png` and `Leaves_Willow_EmissiveTexture.png` are grayscale coverage masks rather than glows. Declaring either would make a willow drape shine flat gray, so this pack binds none of its eleven candidates.

### A large unbound count is a correct outcome

The `candidates` line is a worklist, not an error. Across 22 packs there are 187 unbound candidates, and most fall into three groups:

- **A real map whose albedo no FBX ever binds.** Nine of Casino's 32 are emissives for atlases nothing in the pack wears.
- **A file that is not a companion at all.** Nature's two coverage masks, or `BasePlane_initialShadingGroup_Emissive.png`, named after a Maya default shading group.
- **A shader mask**, which an engine's own water or effect material samples rather than something a glTF channel can bind.

Every candidate should end up either bound or knowingly left; none should merely go unnoticed, which is why the line is printed. The detector is deliberately loose about spelling, because a false positive costs a glance while a false negative means a map nobody notices is missing: Synty ships `Emissive`, `Emmisive`, `Emmision`, `Normals`, `Normal` and one lone `..._Nrml.png`.

## Filling, keeping or dropping untextured models

`--untextured MODE` decides what happens to a material that bound no texture on any channel.

| Mode | Fills a bare material from its flavor set | Drops what is still bare |
| --- | --- | --- |
| `fill` (default) | yes | no |
| `keep` | no | no |
| `drop` | no | yes |
| `fill-or-drop` | yes | yes |

`keep` is the entirely hands-off behavior, and is also how you audit a pack. `drop` writes no GLB for a model whose materials bound no texture on any channel, and deletes one an earlier run left behind.

Three things drop deliberately does not catch:

- **Animation files.** They carry a skeleton and no mesh, so there is nothing to texture. Geometry is the qualifier, which is what keeps the 694 files of the Base Locomotion pack out of it.
- **Foliage bound from `foliage_overrides.json`.** Those bindings are applied before materials are resolved.
- **A model that is only partly untextured.** One textured material is enough to keep it, since the rest may be glass or water.

What it does catch, worth knowing before you use it, is any untextured model that is not a blob: `PolygonSyntyCharacter.fbx` and `SidekickSyntyCharacter.fbx`, the two reference bodies the Base Locomotion animations are built against, bind no texture and are dropped along with the rest. Convert that pack with `--untextured keep` if you want them.

`drop` cannot be combined with `--materials none`, which turns the external-materials path off entirely, so nothing would ever bind a texture and every model would be deleted. `--untextured` also only sees models that go through Blender, so switching modes on an existing conversion wants `--force`.

Across 22 packs, `fill-or-drop` leaves out 380 of 15506 models (2.5%). `drop` alone, with no filling, would remove 929 (6.0%). The gap of 549 models is what flavor sets buy you.
