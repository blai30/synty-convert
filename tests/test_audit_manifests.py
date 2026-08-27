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


def run_audit(materials):
    """Audit a throwaway manifest, with every texture it names present on disk.

    The textures have to exist, otherwise every case would also trip the unrelated
    "manifest texture does not exist" failure and the assertions would not discriminate.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pack = root / "materials" / PACK
        pack.mkdir(parents=True)
        (pack / "materials.json").write_text(
            json.dumps({"pack": PACK, "materials": materials}), encoding="utf-8")
        dst = root / "assets"
        for record in materials:
            target = dst / record["albedo_texture"][len(RES_PREFIX) + 1:]
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


if __name__ == "__main__":
    unittest.main()
