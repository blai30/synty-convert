# Geometry

Everything the converter does to a model's shape rather than its materials.

- [Fixing a pack that converts too small](#fixing-a-pack-that-converts-too-small)
- [LOD chains](#lod-chains)
- [Foliage that names no texture](#foliage-that-names-no-texture)
- [Splitting character heads](#splitting-character-heads)

## Fixing a pack that converts too small

An FBX states the unit its geometry is in, and the converter converts from it. Some Synty packs state the wrong one: the geometry is in meters but the file says centimeters, so every model converts a hundred times under size. Nothing else catches this. The file is valid, the axes are right, the node transforms are identity, and the model is simply too small to see when you drag it into a scene.

The run says so:

```
POLYGON_Dungeon_Pack_SourceFiles_v3: the median model is 0.0245 m across, which means this
pack's FBX declare a unit their geometry is not in. Add a scale for it to
scale_overrides.json and reconvert with --force.
```

`scale` multiplies the conversion the FBX asks for and applies to the whole pack. `files` overrides that for filenames matching a glob, first match winning:

```json
{
  "POLYGON_Dungeon_Pack_SourceFiles_v3": {
    "scale": 100,
    "files": {
      "SM_Item_Chr_*": 1
    }
  }
}
```

Packs are rarely wrong about every file, which is what `files` is for. In the Dungeon pack 780 of 797 models are authored in meters, but the character-held items are genuinely in centimeters and two floor tiles carry a node scale that already compensates. The City pack splits the same way and along its folders: everything under `Models` is in meters, while the characters and vehicles are in centimeters.

A pack entry with only a `files` key goes the other way, for a pack that is fine apart from a handful of models. BattleRoyale, Nature, Dungeons Realms and Fantasy Kingdom each ship a few, and they are the harder ones to notice: one bridge out of four, one grass tuft out of a set, a candle flame.

**How to tell.** For a modular pack, look at its wall pieces: Synty builds on a 5 m grid, so a wall is 500 units in a centimeter pack and 5 in a meter one. For a single model, compare it against its own numbered siblings. `SM_Env_Bridge_01` is 11.00 x 5.00 x 2.79 units where `SM_Env_Bridge_02` is 1100 x 500 x 279, which is the same bridge authored a hundred times over. Both convert without complaint; only one is the right size.

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

## Foliage that names no texture

Synty's Nature Biomes packs export their detailed trees and bushes with the material bindings stripped. One gray Lambert covers the whole model, and because that foliage is built from alpha cutout cards, each quad drawing a leaf texture across the whole of UV space, an untextured card has no coverage to cut with. The model arrives white with solid quads where its leaves should be.

The two halves cannot share a material: a leaf card spans the whole of UV space and the trunk's own UVs sit underneath, so no single image can serve both. They separate cleanly by geometry though. A leaf card is one quad, while a trunk is a single island of hundreds of triangles, so the converter splits on island size and binds each half from `foliage_overrides.json`:

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

| Part | What it is |
| --- | --- |
| `canopy` | The leaf cards, bound as a cutout |
| `trunk` | The woody geometry they hang off, bound opaque |
| `branches` | A separate twig mesh, where a model has one |

Values are path suffixes matched against the pack's shipped textures, exactly as in `texture_overrides.json`, and the first matching glob wins, so put the specific patterns first. A model that names only a `canopy` is not split at all, which is what the pure card bushes want.

```
Foliage   bound 39 mesh(es) across 25 model(s) whose FBX named no texture
```

The file ships mappings for 23 models across MeadowForest and TropicalJungle: the birch, fruit and meadow trees, the forest and pohutukawa trees, and the bushes that go with them.

**How the mappings were arrived at**, since guessing a texture is otherwise exactly what this tool refuses to do. Each pack ships a `Textures/LOD_Cards` image per tree, which is a baked render of the finished model and therefore a picture of the answer. Every candidate leaf map was scored against its card's palette, and the method reproduces the two mappings that were already known independently: `leafPatch_01` for the birches and `pohutukawaLeaf` for the pohutukawas. Trunks were confirmed by sampling each candidate at the trunk's own UV coordinates: on these models a trunk collapses to a single point of the pack atlas, holding the bark color it was authored with. The birches are the exception, their trunks spanning a whole dedicated bark map.

**Two things are deliberately left alone.** Palms, bananas, cacti and succulents in these packs are not card foliage at all but solid geometry wearing a dedicated full-UV texture, so they are a different problem and stay untextured. And in the base Nature pack, `Trunk_FF0000` and `Leave_34FF00` are already separate materials on separate surfaces, so those models need no splitting and are better fixed by hand in Godot.

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

Across eleven packs that is 2 of 154, both meshes whose vendor geometry is already broken before conversion. If a model you want as the player character shows up in that count, check it in Godot with the head hidden before building on it.
