# Troubleshooting

## Blender not found

Pass `--blender /path/to/blender` or set the `BLENDER` environment variable.

## Nothing was converted and no manifest was written

Every model was already up to date. Rerun with `--force`, which is also required after changing `--res-prefix`.

## A whole pack arrives tiny in Godot

Its FBX declare a unit their geometry is not in. See [Fixing a pack that converts too small](geometry.md#fixing-a-pack-that-converts-too-small).

## A model grew instead of shrinking

Expected for small static props. FBX binary deflates its internal arrays while GLB stores raw float buffers, and glTF must split vertices at every normal and UV seam. The GLB gzips well under the original, and Godot recompresses assets when exporting a `.pck`, so shipped size is smaller either way. See [Size expectations](DESIGN.md#size-expectations).

## Materials are untextured, or `.tres` reference a path that does not exist

The assets did not land where the manifest expects. Check `albedo_texture` in `materials/<pack>/materials.json` against where you copied `assets/`. Reconvert with `--res-prefix` set correctly and `--force`.

## `Wrote N materials, 0 failed, N missing textures`

The textures are not in Godot's import cache. Run `godot --headless --import` first, and confirm the assets are at the `res://` location the manifests expect.

## `No pack folder containing materials.json found`

`materials/` is not at `res://materials`. Pass `-- --materials res://your/path`.

If your `godot` command is a wrapper script, check whether it changes the working directory, and pass an absolute `--path /path/to/YourGame` if so.

## A building or prop is flat white or flat gray

Its FBX bound no texture to begin with, so there was nothing to carry across. By default the converter already tries to fix this. Check the model's materials in `materials/<pack>/materials.json`: an entry with `albedo_color` and no `albedo_texture` has no flavor set covering it yet.

- Where the missing atlas is a single obvious texture reference, name it in [texture_overrides.json](materials.md#texture_overridesjson-references-that-will-not-resolve).
- Where the choice is between several shipped alternatives, or the material references no file at all, add a set and a binding to [material_overrides.json](materials.md#material_overridesjson-flavor-sets).
- To leave what is still bare out of the output rather than fix it, convert with [`--untextured drop`](materials.md#filling-keeping-or-dropping-untextured-models).

## A tree or bush is white, with flat quads where its leaves should be

Its FBX binds no texture, so the alpha cutout that shapes each leaf card has nothing to cut with. Add it to [foliage_overrides.json](geometry.md#foliage-that-names-no-texture) and reconvert with `--force`.

Quads spanning the whole canopy are a separate thing: that is the LOD imposter, and it means the model was converted with `--lods keep`.

## A surface looks flat where it should have relief

Check the texture's **Import** dock in Godot shows **Normal Map** under compression.

## A material lost its `_Alpha` suffix in Godot

Expected. Godot treats `_Alpha` as a material name convention, strips it and applies transparency itself, so `PolygonBattleRoyale_Fence_Alpha` shows up as `PolygonBattleRoyale_Fence`. Cosmetic only.

## The run says it repaired some ASCII FBX

Expected, and nothing to act on. Blender's importer reads binary FBX only, and Synty ships a handful of models in the ASCII variant by mistake: a binary file begins `Kaydara FBX Binary`, an ASCII one begins `; FBX 7.x.0 project file`. Across the full catalog this is 21 models, 18 of them in Horror Carnival, and none ships a binary counterpart anywhere in its pack.

The two are the same tree of nodes in different serializations, so the converter transcodes one into the other before importing. Nothing about the model changes, and what Blender built is checked against the counts the source text declares, so a repair that lost or mangled geometry fails that model rather than shipping it. See [ASCII FBX](DESIGN.md#ascii-fbx).

## Godot is importing my source FBX

Keep `synty_packs_fbx/` outside your Godot project, or drop an empty `.gdignore` file in it.

## A `DEAD` line names a binding I know works

`DEAD` is an absence claim, so it is only printed by a run that could have watched the binding fire. If you are seeing it wrongly, the opposite is more likely: check you ran with `--untextured fill` and `--force`. An incremental run only examined the models it reconverted, and says `health not assessed this run` rather than judging.
