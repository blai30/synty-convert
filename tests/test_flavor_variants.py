import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit
import synty_convert

PACK = "FantasyKingdom"
RES_PREFIX = "res://assets"

SETS = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                  "default": "Textures/Atlas_01_A.png"}}


def entry_for(member, **overrides):
    """A worker material record, complete enough for canonical_name to run on it."""
    base = {"source": "lambert1", "color": [1.0, 1.0, 1.0], "alpha": 1.0,
            "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
            "roughness": 0.75, "metallic": 0.5, "normal_strength": 1.0,
            "channels": {"albedo": {"member": member, "texture_source": member}}}
    base.update(overrides)
    return base


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
        entry = entry_for("Textures/Atlas_01_A.png")
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, {}, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0]["name"], "Atlas_01_B_R75_M50")
        self.assertEqual(warnings, [])

    def test_sibling_differs_only_in_name_texture_and_bookkeeping(self):
        self.mirror("Textures/Atlas_01_B.png")
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.748, "metallic": 0.503,
                  "reference": "Atlas_01_A.png", "match": "exact"}
        entry = entry_for("Textures/Atlas_01_A.png")
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, {}, self.output_root, PACK, RES_PREFIX, warnings)
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

    def test_sibling_does_not_carry_alpha_diagnostics_from_its_base(self):
        # alpha_reference/alpha_match describe how an observed material's own alpha
        # channel independently resolved against a file different from its albedo. A
        # generated sibling never resolved anything against an FBX, so carrying these
        # forward from the base it was copied from would describe a resolution that
        # never happened for it, the same bug the bare reference/match pops above exist
        # to prevent.
        self.mirror("Textures/Atlas_01_B.png")
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5,
                  "alpha_reference": "Mask_01_A.png", "alpha_match": "suffix"}
        entry = entry_for("Textures/Atlas_01_A.png")
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, {}, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(len(siblings), 1)
        self.assertNotIn("alpha_reference", siblings[0])
        self.assertNotIn("alpha_match", siblings[0])

    def test_missing_mirrored_file_skips_sibling_and_warns(self):
        # Textures/Atlas_01_B.png is never mirrored into the output.
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = entry_for("Textures/Atlas_01_A.png")
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, {}, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(siblings, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Atlas_01_B", warnings[0])
        self.assertIn(PACK, warnings[0])

    def test_name_not_derived_from_texture_stem_yields_no_variants(self):
        self.mirror("Textures/Atlas_01_B.png")
        record = {"name": "SomeOtherMaterialName", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = entry_for("Textures/Atlas_01_A.png")
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, {}, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(siblings, [])
        self.assertEqual(warnings, [])

    def test_texture_in_no_set_yields_no_variants(self):
        record = {"name": "Horse_01", "used_by": 4, "source_names": ["SM_Horse"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Horse_01.png",
                  "roughness": 0.75, "metallic": 0.5}
        entry = entry_for("Textures/Horse_01.png")
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, {}, self.output_root, PACK, RES_PREFIX, warnings)
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
                    "roughness": 0.55, "metallic": 0.0, "normal_strength": 1.0}

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

    def test_generated_variant_collision_stays_silent_despite_stale_diagnostic_keys(self):
        # A's own emission map was matched by naming heuristic, so A's observed record
        # carries emission_reference/emission_match. C has no emission at all. Neither base
        # declares an emission companion for member B, so both of their generated B siblings
        # render identically -- with no emission of any kind -- and the collision between them
        # must stay silent. Before apply_channels also cleared the diagnostic keys, A's
        # sibling kept its stale emission_reference/emission_match and collided loudly against
        # C's sibling, which never had them, even though every key the Godot generator reads
        # was byte-identical between the two.
        for letter in ("A", "B", "C"):
            self.mirror(f"Textures/Atlas_01_{letter}.png")
        self.mirror("Textures/Emissive_01_A.png")

        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png",
                                       "Textures/Atlas_01_C.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        contexts = {PACK: {"materials": {"sets": sets}}}

        def albedo_channel(letter):
            texture = self.output_root / PACK / "Textures" / f"Atlas_01_{letter}.png"
            return {"texture": str(texture), "texture_source": str(texture),
                    "member": f"Textures/Atlas_01_{letter}.png"}

        emissive_texture = self.output_root / PACK / "Textures" / "Emissive_01_A.png"
        entry_a = {"used_by": 3, "sources": {"SM_Foo"},
                  "channels": {"albedo": albedo_channel("A"),
                               "emission": {"texture": str(emissive_texture),
                                            "texture_source": str(emissive_texture),
                                            "member": "Textures/Emissive_01_A.png",
                                            "reference": "Emissive_01_A.png",
                                            "method": "suffix"}},
                  "color": [1.0, 1.0, 1.0], "alpha": 1.0,
                  "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
                  "roughness": 0.75, "metallic": 0.0, "normal_strength": 1.0}
        entry_c = {"used_by": 2, "sources": {"SM_Bar"},
                  "channels": {"albedo": albedo_channel("C")},
                  "color": [1.0, 1.0, 1.0], "alpha": 1.0,
                  "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
                  "roughness": 0.75, "metallic": 0.0, "normal_strength": 1.0}

        totals = synty_convert.Totals()
        totals.materials = {PACK: {
            "Atlas_01_A_R75": entry_a,
            "Atlas_01_C_R75": entry_c,
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
        self.assertEqual(len(names), 3)
        self.assertIn("Atlas_01_B_R75", names)
        self.assertNotIn("generated twice", output)

        survivor = next(material for material in manifest["materials"]
                        if material["name"] == "Atlas_01_B_R75")
        self.assertNotIn("emission_reference", survivor)
        self.assertNotIn("emission_match", survivor)


COMPANIONS = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01_A.png"},
              "Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01_B.png"}}
NORMAL_COMPANIONS = {"Textures/Atlas_01_A.png": {"normal": "Textures/Normal_01_A.png"},
                     "Textures/Atlas_01_B.png": {"normal": "Textures/Normal_01_B.png"}}


class SiblingCompanions(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def mirror(self, relative):
        target = self.output_root / PACK / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    def variants(self, record, entry, companions, warnings=None):
        if warnings is None:
            warnings = []
        return synty_convert.flavor_variants(record, entry, SETS, companions,
                                             self.output_root, PACK, RES_PREFIX, warnings)

    def test_sibling_takes_its_own_members_emissive_not_the_bases(self):
        # The failure this exists to stop: a _01_B recolor keeping _01_A's glow.
        self.mirror("Textures/Atlas_01_B.png")
        self.mirror("Textures/Emissive_01_A.png")
        self.mirror("Textures/Emissive_01_B.png")
        entry = entry_for("Textures/Atlas_01_A.png")
        entry["channels"]["emission"] = {
            "member": "Textures/Emissive_01_A.png", "method": "companion",
            "texture_source": "Textures/Emissive_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Emissive_01_A.png")}
        record = {"name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
                  "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "emission_texture": "res://assets/FantasyKingdom/Textures/Emissive_01_A.png",
                  "emission_energy": 1.0}
        siblings = self.variants(record, entry, COMPANIONS)
        self.assertEqual(len(siblings), 1)
        self.assertTrue(siblings[0]["emission_texture"].endswith("Emissive_01_B.png"),
                        siblings[0]["emission_texture"])
        # Same name as the sibling that declares no companion at all: a companion is a
        # property of the atlas, so swapping one never renames the material wearing it.
        self.assertEqual(siblings[0]["name"], "Atlas_01_B_R75_M50")

    def test_sibling_whose_member_declares_no_companion_carries_none(self):
        # Inheriting here would put a glow on a recolor the pack never authored one for.
        self.mirror("Textures/Atlas_01_B.png")
        self.mirror("Textures/Emissive_01_A.png")
        entry = entry_for("Textures/Atlas_01_A.png")
        entry["channels"]["emission"] = {
            "member": "Textures/Emissive_01_A.png", "method": "companion",
            "texture_source": "Textures/Emissive_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Emissive_01_A.png")}
        record = {"name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
                  "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "emission_texture": "res://assets/FantasyKingdom/Textures/Emissive_01_A.png",
                  "emission_energy": 1.0}
        only_a = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01_A.png"}}
        siblings = self.variants(record, entry, only_a)
        self.assertEqual(len(siblings), 1)
        self.assertNotIn("emission_texture", siblings[0])
        self.assertNotIn("emission_energy", siblings[0])
        self.assertEqual(siblings[0]["name"], "Atlas_01_B_R75_M50")

    def test_sibling_takes_its_own_members_normal_not_the_bases(self):
        # 23 of the 38 real flavor-affected variants carry a normal map rather than an
        # emissive one, so this channel is the more common case, not the rarer one.
        self.mirror("Textures/Atlas_01_B.png")
        self.mirror("Textures/Normal_01_A.png")
        self.mirror("Textures/Normal_01_B.png")
        entry = entry_for("Textures/Atlas_01_A.png", normal_strength=2.0)
        entry["channels"]["normal"] = {
            "member": "Textures/Normal_01_A.png", "method": "companion",
            "texture_source": "Textures/Normal_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Normal_01_A.png")}
        record = {"name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
                  "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "normal_texture": "res://assets/FantasyKingdom/Textures/Normal_01_A.png",
                  "normal_scale": 2.0}
        siblings = self.variants(record, entry, NORMAL_COMPANIONS)
        self.assertEqual(len(siblings), 1)
        self.assertTrue(siblings[0]["normal_texture"].endswith("Normal_01_B.png"),
                        siblings[0]["normal_texture"])
        self.assertEqual(siblings[0]["normal_scale"], 2.0)
        self.assertEqual(siblings[0]["name"], "Atlas_01_B_R75_M50")

    def test_sibling_whose_member_declares_no_normal_companion_carries_neither(self):
        # Inheriting here would put a bump map on a recolor the pack never authored one for.
        self.mirror("Textures/Atlas_01_B.png")
        self.mirror("Textures/Normal_01_A.png")
        entry = entry_for("Textures/Atlas_01_A.png")
        entry["channels"]["normal"] = {
            "member": "Textures/Normal_01_A.png", "method": "companion",
            "texture_source": "Textures/Normal_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Normal_01_A.png")}
        record = {"name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
                  "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "normal_texture": "res://assets/FantasyKingdom/Textures/Normal_01_A.png",
                  "normal_scale": 1.0}
        only_a = {"Textures/Atlas_01_A.png": {"normal": "Textures/Normal_01_A.png"}}
        siblings = self.variants(record, entry, only_a)
        self.assertEqual(len(siblings), 1)
        self.assertNotIn("normal_texture", siblings[0])
        self.assertNotIn("normal_scale", siblings[0])
        self.assertEqual(siblings[0]["name"], "Atlas_01_B_R75_M50")

    def test_a_companion_not_mirrored_into_the_output_is_not_named(self):
        # The same rule the albedo already follows: a manifest must never name a file that is
        # not on disk, because the Godot generator loads every path it is given. A declared
        # companion that failed to mirror is an authoring mistake in material_overrides.json,
        # so it must warn rather than vanish, the same as the sibling albedo itself does.
        self.mirror("Textures/Atlas_01_B.png")
        entry = entry_for("Textures/Atlas_01_A.png")
        record = {"name": "Atlas_01_A_R75_M50", "used_by": 4, "source_names": ["SM_Foo"],
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "roughness": 0.75, "metallic": 0.5}
        warnings = []
        siblings = self.variants(record, entry, COMPANIONS, warnings)
        self.assertEqual(len(siblings), 1)
        self.assertNotIn("emission_texture", siblings[0])
        self.assertNotIn("Emissive_01_B", siblings[0]["name"])
        self.assertTrue(any("Emissive_01_B" in warning for warning in warnings), warnings)

    def test_cutout_siblings_alpha_channel_follows_its_own_albedo(self):
        # blender_convert binds a cutout material's alpha to the exact same dict object as its
        # albedo. sibling_channels rebuilds a fresh albedo dict for the sibling's own member;
        # the alpha channel must follow it there rather than keep pointing at the base's.
        albedo = {"member": "Textures/Atlas_01_A.png", "texture_source": "Textures/Atlas_01_A.png"}
        entry = entry_for("Textures/Atlas_01_A.png")
        entry["channels"] = {"albedo": albedo, "alpha": albedo}
        channels = synty_convert.sibling_channels(
            entry, "Textures/Atlas_01_B.png", {}, self.output_root, PACK, [])
        self.assertIs(channels["alpha"], channels["albedo"])
        self.assertEqual(channels["alpha"]["member"], "Textures/Atlas_01_B.png")

    def test_base_emission_texture_is_not_resurrected_when_a_sibling_has_no_companion_map(self):
        # The base's glow comes entirely from a map, so nothing about it survives a sibling
        # whose member declares no companion. This is the scenario the old, weaker version of
        # this test could not have caught: it built `record` with the emission keys already
        # holding the correct final answer, so the assertions passed whether or not
        # apply_channels actually cleared anything. Here the copied record starts out with the
        # base's own emission_texture and emission_energy, which only a working clear-then-
        # rewrite removes.
        self.mirror("Textures/Atlas_01_B.png")
        self.mirror("Textures/Emissive_01_A.png")
        entry = entry_for("Textures/Atlas_01_A.png")
        entry["channels"]["emission"] = {
            "member": "Textures/Emissive_01_A.png", "method": "companion",
            "texture_source": "Textures/Emissive_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Emissive_01_A.png")}
        record = {"name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
                  "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "emission_texture": "res://assets/FantasyKingdom/Textures/Emissive_01_A.png",
                  "emission_energy": 1.0}
        siblings = self.variants(record, entry, {})
        self.assertEqual(len(siblings), 1)
        self.assertNotIn("emission_texture", siblings[0])
        self.assertNotIn("emission_color", siblings[0])
        self.assertNotIn("emission_energy", siblings[0])

    def test_emissive_color_is_not_dropped_when_a_member_has_no_companion_map(self):
        # apply_channels reaches a flat emission_color through its
        # `elif any(entry["emission_color"])` branch, which is independent of companions:
        # emission_color is an entry-level value that sibling_channels never touches, so a
        # member declaring no companion still carries the base's declared color. Every other
        # fixture in this suite uses [0.0, 0.0, 0.0], which is falsy and never enters that
        # branch, so nothing else in the suite protects it. apply_channels clears
        # emission_color from the copied sibling before rewriting it, so only a working elif
        # branch puts it back; the record built here deliberately mirrors what a real base
        # material would already carry, since a flat color is not something a sibling recolor
        # ever changes.
        self.mirror("Textures/Atlas_01_B.png")
        entry = entry_for("Textures/Atlas_01_A.png", emission_color=[1.0, 0.0, 0.0],
                          emission_strength=2.5)
        record = {"name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
                  "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
                  "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
                  "emission_color": [1.0, 0.0, 0.0], "emission_energy": 2.5}
        siblings = self.variants(record, entry, {})
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0]["emission_color"], [1.0, 0.0, 0.0])
        self.assertEqual(siblings[0]["emission_energy"], 2.5)


class DiagnosticKeysAgreeWithAudit(unittest.TestCase):
    """Regression test for Minor 3: audit.is_diagnostic must recognize every key
    flavor_variants strips from a generated sibling.

    Both rules encode the same fact, that a sibling never resolved anything against an
    FBX of its own, so it carries no key describing how a reference resolved, but they
    live in two files and are each tested separately today. Nothing asserts the two
    agree, so a future edit to either one, narrowing is_diagnostic's suffix check or
    widening flavor_variants to strip a new diagnostic key, could silently desync them,
    and audit.py would start failing a correct sibling for a key that renders nothing.
    Placed in this file rather than in an audit-specific test file because the fixture
    the assertion needs, a real flavor_variants call producing a real sibling with all
    four channels populated, already lives here for the tests above it.

    The assertion below computes the stripped key set from the difference between the
    base record and the generated sibling, rather than restating the diagnostic key
    names as a literal list, so it is the actual stripping behavior under test, not a
    second copy of the same assumption.
    """

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def mirror(self, relative):
        target = self.output_root / PACK / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    def test_every_key_flavor_variants_strips_is_recognized_by_audit(self):
        self.mirror("Textures/Atlas_01_B.png")
        self.mirror("Textures/Emissive_01_A.png")
        self.mirror("Textures/Normal_01_A.png")
        companions = {
            "Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01_A.png",
                                        "normal": "Textures/Normal_01_A.png"},
            "Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01_A.png",
                                        "normal": "Textures/Normal_01_A.png"},
        }
        entry = entry_for("Textures/Atlas_01_A.png")
        entry["channels"]["alpha"] = {
            "member": "Textures/Mask_01_A.png", "texture_source": "Textures/Mask_01_A.png",
            "reference": "Mask_01_A.psd", "method": "suffix"}
        entry["channels"]["emission"] = {
            "member": "Textures/Emissive_01_A.png",
            "texture_source": "Textures/Emissive_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Emissive_01_A.png"),
            "reference": "Emissive_01_A.psd", "method": "suffix"}
        entry["channels"]["normal"] = {
            "member": "Textures/Normal_01_A.png",
            "texture_source": "Textures/Normal_01_A.png",
            "texture": str(self.output_root / PACK / "Textures/Normal_01_A.png"),
            "reference": "Normal_01_A.psd", "method": "suffix"}
        # Every diagnostic key the real write_manifests loop can produce across all four
        # channels, so the strip has something of each shape to remove.
        record = {
            "name": synty_convert.material_names.canonical_name(entry), "used_by": 4,
            "source_names": ["SM_Foo"], "roughness": 0.75, "metallic": 0.5,
            "albedo_texture": "res://assets/FantasyKingdom/Textures/Atlas_01_A.png",
            "emission_texture": "res://assets/FantasyKingdom/Textures/Emissive_01_A.png",
            "emission_energy": 1.0,
            "normal_texture": "res://assets/FantasyKingdom/Textures/Normal_01_A.png",
            "normal_scale": 1.0,
            "reference": "Atlas_01_A.psd", "match": "exact",
            "alpha_reference": "Mask_01_A.psd", "alpha_match": "suffix",
            "emission_reference": "Emissive_01_A.psd", "emission_match": "suffix",
            "normal_reference": "Normal_01_A.psd", "normal_match": "suffix",
        }
        warnings = []
        siblings = synty_convert.flavor_variants(
            record, entry, SETS, companions, self.output_root, PACK, RES_PREFIX, warnings)
        self.assertEqual(len(siblings), 1)
        self.assertEqual(warnings, [])
        sibling = siblings[0]

        stripped = set(record) - set(sibling)
        # Guard against a vacuous pass: if nothing were actually stripped, every key
        # would trivially be "recognized" by audit for lack of anything to check.
        self.assertTrue(stripped)
        for key in stripped:
            self.assertTrue(audit.is_diagnostic(key),
                            f"flavor_variants strips {key!r} but audit.is_diagnostic "
                            f"does not recognize it as diagnostic")


if __name__ == "__main__":
    unittest.main()
