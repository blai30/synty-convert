import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import material_names


def record(**overrides):
    """A material record with every key canonical_name reads, defaulted to a plain material."""
    base = {"source": "lambert1", "channels": {}, "color": [1.0, 1.0, 1.0], "alpha": 1.0,
            "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
            "roughness": material_names.DEFAULT_ROUGHNESS, "metallic": 0.0,
            "normal_strength": 1.0}
    base.update(overrides)
    return base


def channel(source):
    return {"texture_source": source, "reference": None, "member": source}


class DefaultRoughness(unittest.TestCase):

    def test_matches_blenders_conversion_of_the_fbx_default(self):
        # FBX Shininess defaults to 20 and Blender converts it with 1 - sqrt(shininess) / 10.
        # Pinned because canonical_name uses it to decide whether a material said anything
        # about roughness at all, so a drift here silently requalifies every material name.
        self.assertEqual(material_names.DEFAULT_ROUGHNESS, 0.5528)


class BaseName(unittest.TestCase):

    def test_atlas_filename_wins(self):
        self.assertEqual(
            material_names.base_name(record(channels={"albedo": channel(
                "/packs/FK/Textures/PolygonFantasyKingdom_01_A.png")})),
            "PolygonFantasyKingdom_01_A")

    def test_unresolved_reference_is_cleaned_into_a_name(self):
        self.assertEqual(
            material_names.base_name(record(channels={
                "albedo": {"texture_source": None, "reference": "Wall 01.psd"}})),
            "Wall_01")

    def test_trailing_digits_are_stripped_from_a_maya_name(self):
        # glass, glass1 and glass2 are the same material in three files.
        self.assertEqual(material_names.base_name(record(source="glass2")), "Glass")

    def test_a_name_that_is_only_digits_falls_back(self):
        self.assertEqual(material_names.base_name(record(source="12")), "Material")


class UnsharedTail(unittest.TestCase):

    def test_shared_leading_tokens_are_dropped(self):
        self.assertEqual(
            material_names.unshared_tail("PolygonSciFiSpace_Emissive_01",
                                         "PolygonSciFiSpace_Texture_01_A"),
            "Emissive_01")

    def test_nothing_shared_keeps_the_whole_stem(self):
        self.assertEqual(material_names.unshared_tail("Emissive_01", "Atlas_02"), "Emissive_01")

    def test_a_stem_entirely_shared_keeps_itself_rather_than_vanishing(self):
        self.assertEqual(material_names.unshared_tail("Atlas_01", "Atlas_01"), "Atlas_01")


class CanonicalName(unittest.TestCase):

    def test_plain_material_is_named_for_its_atlas_alone(self):
        self.assertEqual(
            material_names.canonical_name(record(channels={"albedo": channel(
                "/packs/FK/Textures/Atlas_01_A.png")})),
            "Atlas_01_A")

    def test_emissive_map_qualifies_the_name(self):
        self.assertEqual(
            material_names.canonical_name(record(channels={
                "albedo": channel("/packs/SFS/Textures/PolygonSciFiSpace_Texture_01_A.png"),
                "emission": channel("/packs/SFS/Textures/PolygonSciFiSpace_01_Emissive.png")})),
            "PolygonSciFiSpace_Texture_01_A_01_Emissive")

    def test_normal_map_qualifies_the_name(self):
        self.assertEqual(
            material_names.canonical_name(record(channels={
                "albedo": channel("/packs/FK/Textures/Wall_Brick_01.png"),
                "normal": channel("/packs/FK/Textures/Normals/Wall_Brick_01_Normals.png")})),
            "Wall_Brick_01_Normals")

    def test_emissive_colour_qualifies_when_no_map_is_bound(self):
        self.assertEqual(
            material_names.canonical_name(record(
                channels={"albedo": channel("/packs/FK/Textures/Atlas_01_A.png")},
                emission_color=[1.0, 0.0, 0.0])),
            "Atlas_01_A_EmissiveFF0000")

    def test_mask_adds_cutout_and_a_bare_alpha_adds_its_value(self):
        atlas = channel("/packs/FK/Textures/Atlas_01_A.png")
        self.assertEqual(
            material_names.canonical_name(record(channels={"albedo": atlas, "alpha": atlas})),
            "Atlas_01_A_Cutout")
        self.assertEqual(
            material_names.canonical_name(record(
                channels={"albedo": atlas}, alpha=0.45)),
            "Atlas_01_A_A45")

    def test_surface_properties_qualify_only_when_they_depart_from_the_default(self):
        atlas = channel("/packs/FK/Textures/Atlas_01_A.png")
        self.assertEqual(
            material_names.canonical_name(record(
                channels={"albedo": atlas}, roughness=0.75, metallic=0.5)),
            "Atlas_01_A_R75_M50")
        self.assertEqual(
            material_names.canonical_name(record(channels={"albedo": atlas}, metallic=0.0)),
            "Atlas_01_A")

    def test_untextured_material_is_named_for_its_colour(self):
        # Nothing else names the colour, and colour is all an untextured material is.
        self.assertEqual(
            material_names.canonical_name(record(source="Lambert", color=[0.5, 0.5, 0.5],
                                                 alpha=0.45)),
            "Lambert_A45_808080")


if __name__ == "__main__":
    unittest.main()
