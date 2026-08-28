import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import material_flavors

ROOT = os.path.join("packs", "SciFiSpace")

# A stand-in for what texture_matching.index_textures returns: absolute paths under the pack.
TEXTURES = [os.path.join(ROOT, *parts) for parts in (
    ("Textures", "PolygonSciFiSpace_Texture_01_A.png"),
    ("Textures", "PolygonSciFiSpace_01_Emissive.png"),
    ("Textures", "Alts", "PolygonSciFiSpace_02_A.png"),
    ("Textures", "Alts", "PolygonSciFiSpace_02_B.png"),
    ("Textures", "Alts", "PolygonSciFiSpace_02_C.png"),
    ("Textures", "Alts", "PolygonSciFiSpace_02_Emissive.png"),
    ("Textures", "Normals", "Wall_Brick_01_Normals.png"),
    ("Textures", "Castle", "Wall_Brick_01.png"),
)]


class ExpandCompanions(unittest.TestCase):

    def test_one_companion_serves_every_albedo_its_key_matches(self):
        # The whole reason companions are keyed on the atlas rather than on the material:
        # SciFiSpace ships six recolors of one atlas and a single emissive for all of them.
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Alts/PolygonSciFiSpace_02_?.png":
                            {"emission": "Textures/Alts/PolygonSciFiSpace_02_Emissive.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(sorted(companions), ["Textures/Alts/PolygonSciFiSpace_02_A.png",
                                              "Textures/Alts/PolygonSciFiSpace_02_B.png",
                                              "Textures/Alts/PolygonSciFiSpace_02_C.png"])
        for declared in companions.values():
            self.assertEqual(declared,
                             {"emission": "Textures/Alts/PolygonSciFiSpace_02_Emissive.png"})
        self.assertEqual(warnings, [])

    def test_the_emissive_itself_is_not_claimed_as_its_own_albedo(self):
        # "Textures/Alts/*.png" matches the emissive alongside the three recolors, so
        # without the guard the emissive would bind to itself and name a material after it.
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Alts/*.png":
                            {"emission": "Textures/Alts/PolygonSciFiSpace_02_Emissive.png"}}},
            TEXTURES, ROOT, [])
        self.assertNotIn("Textures/Alts/PolygonSciFiSpace_02_Emissive.png", companions)
        self.assertEqual(sorted(companions), ["Textures/Alts/PolygonSciFiSpace_02_A.png",
                                              "Textures/Alts/PolygonSciFiSpace_02_B.png",
                                              "Textures/Alts/PolygonSciFiSpace_02_C.png"])

    def test_both_channels_on_one_albedo(self):
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Castle/Wall_Brick_01.png": {
                "normal": "Textures/Normals/Wall_Brick_01_Normals.png",
                "emission": "Textures/PolygonSciFiSpace_01_Emissive.png"}}},
            TEXTURES, ROOT, [])
        self.assertEqual(companions["Textures/Castle/Wall_Brick_01.png"],
                         {"emission": "Textures/PolygonSciFiSpace_01_Emissive.png",
                          "normal": "Textures/Normals/Wall_Brick_01_Normals.png"})

    def test_key_matching_nothing_warns(self):
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Nope_*.png":
                            {"emission": "Textures/PolygonSciFiSpace_01_Emissive.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(companions, {})
        self.assertTrue(any("matched nothing" in w for w in warnings), warnings)

    def test_value_matching_nothing_warns_and_drops_only_that_channel(self):
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Castle/Wall_Brick_01.png": {
                "emission": "Textures/Nope.png",
                "normal": "Textures/Normals/Wall_Brick_01_Normals.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(companions["Textures/Castle/Wall_Brick_01.png"],
                         {"normal": "Textures/Normals/Wall_Brick_01_Normals.png"})
        self.assertTrue(any("matches 0 textures" in w for w in warnings), warnings)

    def test_ambiguous_value_is_dropped_rather_than_guessed_at(self):
        # A channel binds one file. Picking among several would put a texture nobody chose
        # onto every material wearing the atlas.
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Castle/Wall_Brick_01.png":
                            {"emission": "Textures/Alts/PolygonSciFiSpace_02_?.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(companions, {})
        self.assertTrue(any("matches 3 textures" in w for w in warnings), warnings)

    def test_unknown_channel_warns_and_is_ignored(self):
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {"Textures/Castle/Wall_Brick_01.png":
                            {"roughness": "Textures/Normals/Wall_Brick_01_Normals.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(companions, {})
        self.assertTrue(any("roughness" in w for w in warnings), warnings)

    def test_two_keys_claiming_one_albedo_and_channel_warn_and_the_first_wins(self):
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {
                "Textures/Castle/Wall_Brick_01.png":
                    {"normal": "Textures/Normals/Wall_Brick_01_Normals.png"},
                "Textures/Castle/Wall_*.png":
                    {"normal": "Textures/PolygonSciFiSpace_01_Emissive.png"}}},
            TEXTURES, ROOT, warnings)
        self.assertEqual(companions["Textures/Castle/Wall_Brick_01.png"]["normal"],
                         "Textures/Normals/Wall_Brick_01_Normals.png")
        self.assertTrue(any("overlap" in w for w in warnings), warnings)

    def test_underscore_keys_are_ignored(self):
        # The convention every override file uses for comments and verification notes.
        warnings = []
        companions = material_flavors.expand_companions(
            {"companions": {"_verification": "checked by sampling UVs"}}, TEXTURES, ROOT,
            warnings)
        self.assertEqual(companions, {})
        self.assertEqual(warnings, [])

    def test_absent_table_is_an_empty_mapping(self):
        self.assertEqual(material_flavors.expand_companions({}, TEXTURES, ROOT, []), {})


if __name__ == "__main__":
    unittest.main()
