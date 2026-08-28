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
    """A resolved map. `member` is the pack-relative path a companion target is compared to."""
    return {"texture_source": source, "reference": source, "member": source, "method": "exact"}


def unresolved(reference):
    """A map the FBX named and the pack never shipped, so it settled on no file at all."""
    return {"texture_source": None, "reference": reference, "member": None, "method": None}


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


SCIFI_COMPANIONS = {"Textures/PolygonSciFiSpace_Texture_01_A.png":
                    {"emission": "Textures/PolygonSciFiSpace_01_Emissive.png"}}


class CanonicalName(unittest.TestCase):

    def test_plain_material_is_named_for_its_atlas_alone(self):
        self.assertEqual(
            material_names.canonical_name(record(channels={"albedo": channel(
                "/packs/FK/Textures/Atlas_01_A.png")})),
            "Atlas_01_A")

    def test_a_map_the_atlas_does_not_declare_qualifies_the_name(self):
        # The one thing that can tell two materials on one atlas apart. SciFiSpace ships both:
        # this material binds _02_Emissive while the rest of Texture_01_A takes the declared
        # _01_Emissive, so only this one is named for its map.
        self.assertEqual(
            material_names.canonical_name(
                record(channels={
                    "albedo": channel("Textures/PolygonSciFiSpace_Texture_01_A.png"),
                    "emission": channel("Textures/Alts/PolygonSciFiSpace_02_Emissive.png")}),
                SCIFI_COMPANIONS),
            "PolygonSciFiSpace_Texture_01_A_02_Emissive")

    def test_the_declared_map_leaves_the_name_alone(self):
        # Synty ships Wall_Brick_01.png beside Wall_Brick_01_Normals.png, so the base is the
        # material and the suffix marks a channel. Declaring a companion must not rename a
        # pack's whole material set, and must not name a material after a file that is not
        # its albedo.
        self.assertEqual(
            material_names.canonical_name(
                record(channels={
                    "albedo": channel("Textures/Castle/Wall_Brick_01.png"),
                    "normal": channel("Textures/Normals/Wall_Brick_01_Normals.png")}),
                {"Textures/Castle/Wall_Brick_01.png":
                    {"normal": "Textures/Normals/Wall_Brick_01_Normals.png"}}),
            "Wall_Brick_01")

    def test_a_map_qualifies_when_its_atlas_declares_nothing_on_that_channel(self):
        self.assertEqual(
            material_names.canonical_name(
                record(channels={
                    "albedo": channel("Textures/Castle/Wall_Brick_01.png"),
                    "normal": channel("Textures/Normals/Wall_Brick_01_Normals.png")}),
                SCIFI_COMPANIONS),
            "Wall_Brick_01_Normals")

    def test_an_unresolved_reference_still_qualifies_against_a_declared_companion(self):
        # FantasyKingdom's four HouseRoofNormals/Woods_normals materials name a normal their
        # pack never shipped, which leaves them without the map the rest of the atlas wears.
        # They render differently, so they must not collapse into the plain atlas material,
        # and a channel that settled on no file must never compare equal to a declared one.
        self.assertEqual(
            material_names.canonical_name(
                record(channels={"albedo": channel("Textures/Alts/Atlas_01_A.png"),
                                 "normal": unresolved("HouseRoofNormals.png")}),
                {"Textures/Alts/Atlas_01_A.png": {"normal": "Textures/Normals/Atlas_01_Normals.png"}}),
            "Atlas_01_A_HouseRoofNormals")

    def test_naming_the_declared_map_yourself_still_leaves_the_name_alone(self):
        # AridDesert has one material naming PolygonFantasyGothic_Emissive_01_A.png, which
        # resolves to the very emissive its atlas declares. Qualifying on it would split that
        # material off from the 100 atlas-mates it renders identically to. What decides the
        # name is the map it settled on, never the reference that found it, which stays on
        # the record for the report to review.
        named = dict(channel("Textures/AridDesert_Emissive_01_A.png"),
                     reference="PolygonFantasyGothic_Emissive_01_A.png", method="tokens")
        self.assertEqual(
            material_names.canonical_name(
                record(channels={"albedo": channel("Textures/AridDesert_Texture_01.png"),
                                 "emission": named}),
                {"Textures/AridDesert_Texture_01.png":
                    {"emission": "Textures/AridDesert_Emissive_01_A.png"}}),
            "AridDesert_Texture_01")

    def test_a_bound_map_suppresses_the_emissive_color_even_when_it_qualifies_nothing(self):
        # build_material discards a declared emission color the moment a map covers it, so
        # this material renders exactly as its atlas-mates do and has to share their name.
        # Suppressing the declared map's qualifier must not let the dead color name it.
        self.assertEqual(
            material_names.canonical_name(
                record(channels={"albedo": channel("Textures/Atlas_01_A.png"),
                                 "emission": channel("Textures/Atlas_01_Emissive.png")},
                       emission_color=[1.0, 0.0, 0.0]),
                {"Textures/Atlas_01_A.png": {"emission": "Textures/Atlas_01_Emissive.png"}}),
            "Atlas_01_A")

    def test_emissive_color_qualifies_when_no_map_is_bound(self):
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

    def test_untextured_material_is_named_for_its_color(self):
        # Nothing else names the color, and color is all an untextured material is.
        self.assertEqual(
            material_names.canonical_name(record(source="Lambert", color=[0.5, 0.5, 0.5],
                                                 alpha=0.45)),
            "Lambert_A45_808080")


if __name__ == "__main__":
    unittest.main()
