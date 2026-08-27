import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import material_flavors


def record(name, method="flavor", binding_model="*", binding="lambert1", flavor="Atlas"):
    """One entry of the dict resolve_materials hands back, keyed by the FBX material name."""
    albedo = {"method": method, "texture": "/out/atlas.png"}
    if method == "flavor":
        albedo.update({"binding_model": binding_model, "binding": binding, "flavor": flavor})
    return {"name": name, "channels": {"albedo": albedo}}


class FlavorFillsReportsWhatFilled(unittest.TestCase):
    """The per model fill list the report counts from, before any name deduplication.

    This is pure dictionary work over records the worker already built, so it belongs
    beside the rest of the flavor logic where it can be tested, rather than in the Blender
    worker where importing it at all requires bpy.
    """

    def test_nothing_filled_gives_nothing(self):
        resolved = {"a": record("Atlas_01_A", method="exact")}
        self.assertEqual(material_flavors.flavor_fills(resolved), [])

    def test_a_fill_carries_its_binding_and_flavor(self):
        resolved = {"lambert1": record("Texture_01_A", binding_model="SM_Veh_*")}
        self.assertEqual(material_flavors.flavor_fills(resolved), [
            {"binding_model": "SM_Veh_*", "binding": "lambert1",
             "name": "Texture_01_A", "flavor": "Atlas"}])

    def test_two_materials_filling_one_binding_alike_count_once(self):
        # A model whose two slots both fill the same binding onto the same texture is
        # still one model in the report, so the pair collapses here.
        resolved = {"lambert1": record("Texture_01_A"), "lambert1_copy": record("Texture_01_A")}
        self.assertEqual(len(material_flavors.flavor_fills(resolved)), 1)

    def test_same_binding_onto_different_textures_stays_separate(self):
        resolved = {"a": record("Texture_01_A"), "b": record("Texture_02_A")}
        self.assertEqual([fill["name"] for fill in material_flavors.flavor_fills(resolved)],
                         ["Texture_01_A", "Texture_02_A"])

    def test_non_flavor_channels_are_ignored(self):
        resolved = {"a": record("Atlas_01_A", method="override"),
                    "b": record("Texture_01_A")}
        self.assertEqual([fill["name"] for fill in material_flavors.flavor_fills(resolved)],
                         ["Texture_01_A"])

    def test_a_material_with_no_albedo_channel_does_not_crash(self):
        resolved = {"a": {"name": "ColourOnly", "channels": {}}}
        self.assertEqual(material_flavors.flavor_fills(resolved), [])

    def test_two_bindings_sharing_a_material_glob_stay_separate(self):
        # The distinction the DEAD check depends on: a narrow model-scoped rule and a broad
        # one can share a material glob, and collapsing them would hide a stale rule.
        resolved = {"a": record("Texture_01_A", binding_model="SM_Veh_*"),
                    "b": record("Texture_01_A", binding_model="SM_Prop_*")}
        self.assertEqual(sorted(fill["binding_model"]
                                for fill in material_flavors.flavor_fills(resolved)),
                         ["SM_Prop_*", "SM_Veh_*"])


if __name__ == "__main__":
    unittest.main()
