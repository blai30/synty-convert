import collections
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit

RES_PREFIX = "res://assets"
PACK = "TestPack"


def entry(name, texture, **extra):
    """A manifest record shaped like the ones write_manifests emits."""
    record = {"name": name, "used_by": 0, "source_names": [],
              "albedo_texture": f"{RES_PREFIX}/{PACK}/Textures/{texture}",
              "roughness": 0.5528, "metallic": 0.0}
    record.update(extra)
    return record


def run_audit(materials, missing_textures=()):
    """Audit a throwaway manifest, with every texture it names present on disk.

    The textures have to exist, otherwise every case would also trip the unrelated
    "manifest texture does not exist" failure and the assertions would not discriminate.
    A texture named in `missing_textures` is deliberately left off disk, for a case that
    wants to exercise that failure on purpose.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pack = root / "materials" / PACK
        pack.mkdir(parents=True)
        (pack / "materials.json").write_text(
            json.dumps({"pack": PACK, "materials": materials}), encoding="utf-8")
        dst = root / "assets"
        for record in materials:
            for key in ("albedo_texture", "emission_texture", "normal_texture"):
                texture = record.get(key)
                if not texture or texture in missing_textures:
                    continue
                target = dst / texture[len(RES_PREFIX) + 1:]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"")
        failures = collections.defaultdict(list)
        stats = collections.Counter()
        stats["textures"] = set()
        audit.audit_manifests(root / "materials", dst, RES_PREFIX, failures, stats)
        return failures, stats


class VariantMustWearADifferentTexture(unittest.TestCase):
    """A variant that kept its base's texture makes the whole swap feature a no-op.

    The allowed-difference set has to permit albedo_texture to differ, since that is the
    one thing a variant is for. Permitting it is not the same as requiring it: a generated
    sibling that resolved back onto the base's own texture is indistinguishable from the
    base in Godot, and until this check existed it passed the audit silently.
    """

    def test_variant_sharing_its_base_texture_fails(self):
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png"),
            entry("Roof_Tile_02", "Roof_Tile_01.png", variant_of="Roof_Tile_01"),
        ])
        self.assertEqual(list(failures), ["variant wears the same texture as its base"])
        self.assertIn("Roof_Tile_02", failures["variant wears the same texture as its base"][0])

    def test_variant_wearing_its_own_texture_passes(self):
        failures, stats = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png"),
            entry("Roof_Tile_02", "Roof_Tile_02.png", variant_of="Roof_Tile_01"),
        ])
        self.assertEqual(dict(failures), {})
        self.assertEqual(stats["variants"], 1)

    def test_a_base_sharing_a_texture_with_a_non_variant_is_not_flagged(self):
        # Two independently observed materials may legitimately wear one texture; only the
        # variant relationship makes sameness a defect.
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png"),
            entry("Roof_Tile_01_Rough", "Roof_Tile_01.png", roughness=0.9),
        ])
        self.assertEqual(dict(failures), {})


class ExistingVariantInvariantsStillHold(unittest.TestCase):
    """The three invariants Task 10 added, guarded here so the new check cannot mask them."""

    def test_variant_differing_beyond_its_texture_fails(self):
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png"),
            entry("Roof_Tile_02", "Roof_Tile_02.png", variant_of="Roof_Tile_01", roughness=0.9),
        ])
        self.assertEqual(list(failures), ["variant differs from its base beyond its texture"])
        self.assertIn("roughness",
                      failures["variant differs from its base beyond its texture"][0])

    def test_variant_of_naming_no_entry_fails(self):
        failures, _ = run_audit([
            entry("Roof_Tile_02", "Roof_Tile_02.png", variant_of="Roof_Tile_01"),
        ])
        self.assertEqual(list(failures), ["variant_of names no entry in the manifest"])

    def test_duplicate_names_fail(self):
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png"),
            entry("Roof_Tile_01", "Roof_Tile_02.png"),
        ])
        self.assertIn("two materials share a name", failures)


class VariantMayDifferFromItsBaseByDiagnostics(unittest.TestCase):
    """A generated sibling has no FBX reference of its own, so flavor_variants strips every
    diagnostic key from it before it reaches the manifest. The base keeps its own diagnostics,
    so a sibling differs from its base on every one of them, including alpha_reference and
    alpha_match. None of that describes a bug; it describes a resolution that never happened
    for a material nothing observed.
    """

    def test_variant_differing_only_by_a_diagnostic_key_is_accepted(self):
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png",
                  reference="Roof_Tile_01.png", match="exact",
                  alpha_reference="Roof_Tile_01.png", alpha_match="exact"),
            entry("Roof_Tile_02", "Roof_Tile_02.png", variant_of="Roof_Tile_01"),
        ])
        self.assertEqual(dict(failures), {})

    def test_variant_differing_by_a_value_key_is_still_reported(self):
        # Widening VARIANT_KEYS to exempt diagnostics must not exempt everything else; a
        # genuine value key such as roughness has to keep failing the audit.
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png",
                  reference="Roof_Tile_01.png", match="exact"),
            entry("Roof_Tile_02", "Roof_Tile_02.png", variant_of="Roof_Tile_01", roughness=0.9),
        ])
        self.assertEqual(list(failures), ["variant differs from its base beyond its texture"])
        self.assertIn("roughness",
                      failures["variant differs from its base beyond its texture"][0])


class CompanionTexturesAreAudited(unittest.TestCase):
    """emission_texture and normal_texture are consumed exactly like albedo_texture: the
    Godot generator loads every path a manifest names, and a sibling's companion is its own
    member's rather than something inherited from its base.
    """

    def test_an_emission_texture_naming_a_missing_file_is_reported(self):
        # The Godot generator loads every path a manifest names, so a manifest that names a
        # file the mirror does not hold produces a material that is silently untextured.
        texture = f"{RES_PREFIX}/{PACK}/Textures/Emissive/Roof_Tile_01_Emissive.png"
        failures, _ = run_audit(
            [entry("Roof_Tile_01", "Roof_Tile_01.png", emission_texture=texture)],
            missing_textures={texture})
        self.assertIn("manifest texture does not exist", failures)

    def test_a_variant_with_no_emission_where_its_base_had_one_is_accepted(self):
        # Not every member of a set has a companion, and dropping one is correct rather than
        # a missing key.
        failures, _ = run_audit([
            entry("Roof_Tile_01", "Roof_Tile_01.png",
                  emission_texture=f"{RES_PREFIX}/{PACK}/Textures/Emissive/Roof_Tile_01_Emissive.png"),
            entry("Roof_Tile_02", "Roof_Tile_02.png", variant_of="Roof_Tile_01"),
        ])
        self.assertEqual(dict(failures), {})


if __name__ == "__main__":
    unittest.main()
