import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import material_flavors

ROOT = os.path.join("packs", "FantasyKingdom")

# A stand-in for what texture_matching.index_textures returns: absolute paths under the pack.
TEXTURES = [os.path.join(ROOT, *parts) for parts in (
    ("Textures", "Castle", "Wall_Brick_01.png"),
    ("Textures", "Castle", "Wall_Stucco_01.png"),
    ("Textures", "Castle", "Wall_Evil_01.png"),
    ("Textures", "Castle", "Roof_Tile_01.png"),
    ("Textures", "Castle", "Roof_Slat_01.png"),
    ("Textures", "Normals", "Wall_Brick_01_Normal.png"),
    ("Textures", "Alts", "PolygonFantasyKingdom_01_A.png"),
    ("Textures", "Alts", "PolygonFantasyKingdom_01_B.png"),
)]


class ExpandSets(unittest.TestCase):

    def test_glob_is_scoped_to_its_folder(self):
        # Textures/Normals must not leak into a Textures/Castle set.
        warnings = []
        sets = material_flavors.expand_sets(
            {"flavors": {"Wall": {"members": ["Textures/Castle/Wall_*.png"],
                                  "default": "Wall_Brick_01.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(sets["Wall"]["members"],
                         ["Textures/Castle/Wall_Brick_01.png",
                          "Textures/Castle/Wall_Evil_01.png",
                          "Textures/Castle/Wall_Stucco_01.png"])
        self.assertEqual(sets["Wall"]["default"], "Textures/Castle/Wall_Brick_01.png")
        self.assertEqual(warnings, [])

    def test_default_must_match_on_a_path_boundary(self):
        # A member whose filename merely ends with the default's filename is not that
        # default. The fixture is synthetic because no shipped pack pairs names this way
        # today, but without the boundary both members would resolve as the default, the
        # set would be dropped as ambiguous, and every model bound to it would go back to
        # flat white with no error anywhere.
        textures = [os.path.join(ROOT, "Textures", "Castle", name) for name in
                    ("Wall_Brick_01.png", "Reinforced_Wall_Brick_01.png")]
        warnings = []
        sets = material_flavors.expand_sets(
            {"flavors": {"Wall": {"members": ["Textures/Castle/*Wall_Brick_01.png"],
                                  "default": "Wall_Brick_01.png"}}},
            textures, ROOT, warnings)
        self.assertEqual(sets["Wall"]["default"], "Textures/Castle/Wall_Brick_01.png")
        self.assertEqual(warnings, [])

    def test_several_globs_merge_into_one_set(self):
        warnings = []
        sets = material_flavors.expand_sets(
            {"flavors": {"Roof": {"members": ["Textures/Castle/Roof_Tile_*.png",
                                              "Textures/Castle/Roof_Slat_*.png"],
                                  "default": "Roof_Tile_01.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(sets["Roof"]["members"],
                         ["Textures/Castle/Roof_Slat_01.png",
                          "Textures/Castle/Roof_Tile_01.png"])
        self.assertEqual(warnings, [])

    def test_glob_matching_nothing_warns(self):
        warnings = []
        material_flavors.expand_sets(
            {"flavors": {"Wall": {"members": ["Textures/Castle/Nope_*.png"],
                                  "default": "Nope_01.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertTrue(any("matched nothing" in w for w in warnings), warnings)

    def test_default_outside_its_members_warns_and_drops_the_set(self):
        warnings = []
        sets = material_flavors.expand_sets(
            {"flavors": {"Wall": {"members": ["Textures/Castle/Wall_*.png"],
                                  "default": "Roof_Tile_01.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertNotIn("Wall", sets)
        self.assertTrue(any("default" in w for w in warnings), warnings)

    def test_overlapping_sets_warn(self):
        warnings = []
        material_flavors.expand_sets(
            {"flavors": {"A": {"members": ["Textures/Castle/Wall_*.png"],
                               "default": "Wall_Brick_01.png"},
                         "B": {"members": ["Textures/Castle/Wall_Brick_*.png"],
                               "default": "Wall_Brick_01.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertTrue(any("overlap" in w for w in warnings), warnings)


SETS = {"Wall": {"members": ["Textures/Castle/Wall_Brick_01.png"],
                 "default": "Textures/Castle/Wall_Brick_01.png"},
        "Atlas": {"members": ["Textures/Alts/PolygonFantasyKingdom_01_A.png"],
                  "default": "Textures/Alts/PolygonFantasyKingdom_01_A.png"}}

CONFIG = {"bind": [
    {"model": "SM_Bld_Preset_*", "material": "PolygonCastle_GLASS", "flavor": "Atlas"},
    {"material": "Wall*", "flavor": "Wall"},
]}


class Bindings(unittest.TestCase):

    def setUp(self):
        self.warnings = []
        self.bindings = material_flavors.normalize_bindings(CONFIG, SETS, self.warnings)

    def test_absent_keys_default_to_match_anything(self):
        self.assertEqual(self.bindings[1]["model"], "*")

    def test_material_glob_is_case_insensitive(self):
        # The same surface is spelled Wall, WALL and Wall42 across the pack's FBX files.
        for source in ("Wall", "WALL", "Wall42", "wall1"):
            found = material_flavors.match_binding(self.bindings, "SM_Bld_Castle_Wall_01", source)
            self.assertEqual(found["flavor"], "Wall", source)

    def test_model_glob_narrows_a_shared_material_name(self):
        # PolygonCastle_GLASS is the misnamed sole material on the preset buildings and a
        # real glass material elsewhere in the pack, so the binding is scoped by filename.
        found = material_flavors.match_binding(
            self.bindings, "SM_Bld_Preset_Tavern_01_Optimized", "PolygonCastle_GLASS")
        self.assertEqual(found["flavor"], "Atlas")
        self.assertIsNone(material_flavors.match_binding(
            self.bindings, "SM_Prop_Lantern_01", "PolygonCastle_GLASS"))

    def test_first_match_wins(self):
        bindings = material_flavors.normalize_bindings(
            {"bind": [{"material": "Wall*", "flavor": "Atlas"},
                      {"material": "Wall*", "flavor": "Wall"}]}, SETS, [])
        self.assertEqual(material_flavors.match_binding(bindings, "any", "Wall")["flavor"],
                         "Atlas")

    def test_binding_naming_an_unknown_set_warns_and_is_dropped(self):
        warnings = []
        bindings = material_flavors.normalize_bindings(
            {"bind": [{"material": "Wall*", "flavor": "Nope"}]}, SETS, warnings)
        self.assertEqual(bindings, [])
        self.assertTrue(any("Nope" in w for w in warnings), warnings)

    def test_no_match_returns_none(self):
        self.assertIsNone(material_flavors.match_binding(self.bindings, "any", "Glass"))

    def test_model_glob_is_case_insensitive(self):
        found = material_flavors.match_binding(
            self.bindings, "sm_bld_preset_tavern_01_optimized", "PolygonCastle_GLASS")
        self.assertEqual(found["flavor"], "Atlas")

    def test_dropping_an_unknown_set_preserves_the_order_of_its_neighbours(self):
        warnings = []
        bindings = material_flavors.normalize_bindings(
            {"bind": [{"material": "Wall*", "flavor": "Wall"},
                      {"material": "Ghost*", "flavor": "Nope"},
                      {"material": "Glass*", "flavor": "Atlas"}]}, SETS, warnings)
        self.assertEqual([binding["material"] for binding in bindings], ["Wall*", "Glass*"])
        self.assertEqual(len(warnings), 1)


class Variants(unittest.TestCase):

    SETS = {"Wall": {"members": ["Textures/Castle/Wall_Brick_01.png",
                                 "Textures/Castle/Wall_Evil_01.png",
                                 "Textures/Castle/Wall_Stucco_01.png"],
                     "default": "Textures/Castle/Wall_Brick_01.png"}}

    def test_siblings_exclude_the_member_itself(self):
        self.assertEqual(
            material_flavors.variants_of("Textures/Castle/Wall_Brick_01.png", self.SETS),
            ["Textures/Castle/Wall_Evil_01.png", "Textures/Castle/Wall_Stucco_01.png"])

    def test_a_texture_in_no_set_has_no_siblings(self):
        self.assertEqual(
            material_flavors.variants_of("Textures/Misc/Horse_01.png", self.SETS), [])

    def test_overlapping_sets_resolve_to_the_alphabetically_first(self):
        # expand_sets warns about overlap but still returns both sets, so variants_of has
        # to break the tie deterministically. It sorts the names rather than trusting dict
        # order. The keys here are inserted out of alphabetical order on purpose, so a
        # plain "for name in sets" would return FromB and fail this.
        sets = {"B": {"members": ["Textures/Castle/FromB.png", "Textures/Castle/Shared.png"],
                      "default": "Textures/Castle/Shared.png"},
                "A": {"members": ["Textures/Castle/FromA.png", "Textures/Castle/Shared.png"],
                      "default": "Textures/Castle/Shared.png"}}
        self.assertEqual(
            material_flavors.variants_of("Textures/Castle/Shared.png", sets),
            ["Textures/Castle/FromA.png"])


if __name__ == "__main__":
    unittest.main()
