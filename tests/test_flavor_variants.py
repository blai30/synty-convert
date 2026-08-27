import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synty_convert

PACK = "FantasyKingdom"
RES_PREFIX = "res://assets"

SETS = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                  "default": "Textures/Atlas_01_A.png"}}


class FlavorVariants(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def mirror(self, relative):
        """Create a real file on disk, standing in for a texture already mirrored to output."""
        target = self.output_root / PACK / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    def test_sibling_inherits_base_qualifiers(self):
        self.mirror("Textures/Atlas_01_B.png")
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = {"channels": {"albedo": {"member": "Textures/Atlas_01_A.png"}}}
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0]["name"], "Atlas_01_B_R75_M50")
        self.assertEqual(warnings, [])

    def test_sibling_differs_only_in_name_texture_and_bookkeeping(self):
        self.mirror("Textures/Atlas_01_B.png")
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.748, "metallic": 0.503,
                  "reference": "Atlas_01_A.png", "match": "exact"}
        entry = {"channels": {"albedo": {"member": "Textures/Atlas_01_A.png"}}}
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(len(siblings), 1)
        sibling = siblings[0]
        self.assertEqual(sibling["name"], "Atlas_01_B_R75_M50")
        self.assertNotEqual(sibling["albedo_texture"], record["albedo_texture"])
        self.assertEqual(sibling["used_by"], 0)
        self.assertEqual(sibling["variant_of"], "Atlas_01_A_R75_M50")
        self.assertEqual(sibling["source_names"], [])
        self.assertNotIn("reference", sibling)
        self.assertNotIn("match", sibling)
        # roughness and metallic are surface properties earned by the observed material,
        # and a sibling wears them unchanged.
        self.assertEqual(sibling["roughness"], record["roughness"])
        self.assertEqual(sibling["metallic"], record["metallic"])

    def test_missing_mirrored_file_skips_sibling_and_warns(self):
        # Textures/Atlas_01_B.png is never mirrored into the output.
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = {"channels": {"albedo": {"member": "Textures/Atlas_01_A.png"}}}
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(siblings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Atlas_01_B", warnings[0])
        self.assertIn(PACK, warnings[0])

    def test_name_not_derived_from_texture_stem_yields_no_variants(self):
        self.mirror("Textures/Atlas_01_B.png")
        record = {"name": "SomeOtherMaterialName", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = {"channels": {"albedo": {"member": "Textures/Atlas_01_A.png"}}}
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(siblings, [])
        self.assertEqual(warnings, [])

    def test_texture_in_no_set_yields_no_variants(self):
        record = {"name": "Horse_01", "used_by": 4, "source_names": ["SM_Horse"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Horse_01.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = {"channels": {"albedo": {"member": "Textures/Horse_01.png"}}}
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(siblings, [])
        self.assertEqual(warnings, [])

    def test_generated_variant_collision_warns_and_keeps_first(self):
        # Two observed materials whose roughness rounds to the same qualifier text, on
        # different members of a three-way flavor set, so their generated siblings collide
        # with each other rather than with an observed material.
        for letter in ("A", "B", "C"):
            self.mirror(f"Textures/Atlas_01_{letter}.png")

        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png",
                                       "Textures/Atlas_01_C.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        contexts = {PACK: {"materials": {"sets": sets}}}

        def albedo_channel(letter):
            texture = self.output_root / PACK / "Textures" / f"Atlas_01_{letter}.png"
            return {"texture": str(texture), "texture_source": str(texture),
                    "member": f"Textures/Atlas_01_{letter}.png"}

        def material_entry(letter, used_by, source, roughness):
            return {"used_by": used_by, "sources": {source},
                    "channels": {"albedo": albedo_channel(letter)},
                    "color": [1.0, 1.0, 1.0], "alpha": 1.0,
                    "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
                    "roughness": roughness, "metallic": 0.0, "normal_strength": 1.0}

        totals = synty_convert.Totals()
        totals.materials = {PACK: {
            "Atlas_01_A_R75": material_entry("A", 3, "SM_Foo", 0.748),
            "Atlas_01_C_R75": material_entry("C", 2, "SM_Bar", 0.752),
        }}

        with tempfile.TemporaryDirectory() as materials_dir:
            materials_root = Path(materials_dir)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                synty_convert.write_manifests(
                    totals, materials_root, self.output_root, RES_PREFIX, contexts)
            output = stdout.getvalue()
            manifest = json.loads(
                (materials_root / PACK / "materials.json").read_text(encoding="utf-8"))

        names = [material["name"] for material in manifest["materials"]]

        # Guard against a vacuous test: two observed materials plus one surviving generated
        # sibling. If the existence check had skipped every sibling, this would be 2, the
        # collision could never occur, and the assertions below would pass for no reason.
        self.assertEqual(len(names), 3)
        self.assertIn("Atlas_01_B_R75", names)

        self.assertIn("\n1 flavor variant warning(s):", output)
        self.assertIn(
            f"{PACK}: flavor variant Atlas_01_B_R75 generated twice with differing "
            f"roughness, from Atlas_01_A_R75 and Atlas_01_C_R75; keeping the first", output)

        survivors = [material for material in manifest["materials"]
                     if material["name"] == "Atlas_01_B_R75"]
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0]["roughness"], 0.748)

        # The two collisions against observed materials (Atlas_01_A_R75 and
        # Atlas_01_C_R75 both already existed) must stay silent.
        self.assertNotIn("flavor variant Atlas_01_A_R75 generated twice", output)
        self.assertNotIn("flavor variant Atlas_01_C_R75 generated twice", output)

    def test_generated_variant_collision_stays_silent_when_identical(self):
        # Two observed materials in one set that render identically in every rendered
        # property, so their generated siblings collide but neither is losing anything.
        for letter in ("A", "B", "C"):
            self.mirror(f"Textures/Atlas_01_{letter}.png")

        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png",
                                       "Textures/Atlas_01_C.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        contexts = {PACK: {"materials": {"sets": sets}}}

        def albedo_channel(letter):
            texture = self.output_root / PACK / "Textures" / f"Atlas_01_{letter}.png"
            return {"texture": str(texture), "texture_source": str(texture),
                    "member": f"Textures/Atlas_01_{letter}.png"}

        def material_entry(letter, used_by, source):
            return {"used_by": used_by, "sources": {source},
                    "channels": {"albedo": albedo_channel(letter)},
                    "color": [1.0, 1.0, 1.0], "alpha": 1.0,
                    "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
                    "roughness": 0.5528, "metallic": 0.0, "normal_strength": 1.0}

        totals = synty_convert.Totals()
        totals.materials = {PACK: {
            "Atlas_01_A_R55": material_entry("A", 3, "SM_Foo"),
            "Atlas_01_C_R55": material_entry("C", 2, "SM_Bar"),
        }}

        with tempfile.TemporaryDirectory() as materials_dir:
            materials_root = Path(materials_dir)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                synty_convert.write_manifests(
                    totals, materials_root, self.output_root, RES_PREFIX, contexts)
            output = stdout.getvalue()
            manifest = json.loads(
                (materials_root / PACK / "materials.json").read_text(encoding="utf-8"))

        names = [material["name"] for material in manifest["materials"]]

        # Guard against a vacuous test: two observed materials plus one surviving generated
        # sibling, same shape as the differing case above.
        self.assertEqual(len(names), 3)
        self.assertIn("Atlas_01_B_R55", names)

        self.assertNotIn("generated twice", output)

        survivors = [material for material in manifest["materials"]
                     if material["name"] == "Atlas_01_B_R55"]
        self.assertEqual(len(survivors), 1)


if __name__ == "__main__":
    unittest.main()
