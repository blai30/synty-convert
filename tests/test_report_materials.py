import collections
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synty_convert

PACK = "FantasyKingdom"


def material_entry(name, method, reference, used_by=3, source="SM_Castle_Evil_01"):
    """A minimal totals.materials record, shaped like the real thing.

    Real records carry their own "name", duplicating the dict key they are stored under
    (see resolve_materials in blender_convert.py), so fixtures do the same rather than
    leaving that key out.
    """
    return {"name": name, "used_by": used_by, "sources": {source},
            "channels": {"albedo": {"method": method, "reference": reference,
                                    "texture": "/out/whatever.png"}}}


def run_report(totals, contexts):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        synty_convert.report_materials(totals, contexts)
    return stdout.getvalue()


class FilledSurvivesTheMerge(unittest.TestCase):
    """Regression test for the race the previous fix repaired.

    A flavor fill renames itself after its texture and can merge with an identically named
    record that resolved normally; whichever arrived first wins the merge, so
    totals.materials can hold a record with no `method: "flavor"` marker even though the
    fill genuinely happened. report_materials must read totals.filled, counted per model
    before that merge, rather than re-deriving "filled" from the merged dict.
    """

    def setUp(self):
        self.totals = synty_convert.Totals()
        # The merge kept the normally-resolved copy: no "flavor" method survives here,
        # even though the fill (recorded separately in totals.filled) did happen.
        self.totals.materials = {PACK: {
            "Wall_Evil_01": material_entry("Wall_Evil_01", "exact", "Wall_Evil_01.png")}}
        self.contexts = {PACK: {"materials": {
            "sets": {"Wall": {"members": ["Textures/Wall_Evil_01.png"],
                              "default": "Textures/Wall_Brick_01.png"}},
            "bind": [{"model": "*", "material": "Wall*", "flavor": "Wall"}],
        }}}

    def test_fill_is_reported_and_the_binding_is_not_dead(self):
        self.totals.filled = collections.Counter(
            {(PACK, "*", "Wall*", "Wall_Evil_01", "Wall"): 3})
        output = run_report(self.totals, self.contexts)
        self.assertIn("filled   Wall*", output)
        self.assertIn("-> Wall_Evil_01", output)
        self.assertIn("flavor Wall)", output)
        self.assertNotIn("DEAD", output)

    def test_binding_absent_from_filled_is_reported_dead(self):
        # Same shape as above, but nothing ever recorded a fill for this binding.
        self.totals.filled = collections.Counter()
        output = run_report(self.totals, self.contexts)
        dead_lines = [line for line in output.splitlines() if "DEAD" in line]
        self.assertEqual(len(dead_lines), 1)
        self.assertIn("Wall*", dead_lines[0])
        self.assertIn("'*'", dead_lines[0])


class DeadChecksModelAndMaterialTogether(unittest.TestCase):
    """Regression test for Fix 2: DEAD must key on (model, material), not material alone.

    normalize_bindings's docstring anticipates configs where a narrow model-scoped rule
    sits above a broader one sharing the same material glob. Before this fix, `fired` was
    a set of material globs alone, so once the broad rule fired, the narrow rule's material
    string was already in the set and it read as alive even though its own model glob never
    matched anything. This is the case that escaped the manual scan which caught the
    original bug.
    """

    def test_narrow_binding_sharing_a_material_glob_is_dead_when_only_the_broad_one_fired(self):
        totals = synty_convert.Totals()
        totals.materials = {PACK: {"Atlas_01_A": material_entry(
            "Atlas_01_A", "flavor", None, source="SM_Bld_Preset_Tavern_01")}}
        # Only the broad, preset-scoped binding fired; the prop-scoped one shares the same
        # material glob but never appears in totals.filled.
        totals.filled = collections.Counter(
            {(PACK, "SM_Bld_Preset_*", "PolygonCastle_GLASS", "Atlas_01_A", "Atlas"): 1})
        contexts = {PACK: {"materials": {
            "sets": {"Atlas": {"members": ["Textures/Atlas_01_A.png"],
                               "default": "Textures/Atlas_01_A.png"}},
            "bind": [
                {"model": "SM_Bld_Preset_*", "material": "PolygonCastle_GLASS", "flavor": "Atlas"},
                {"model": "SM_Prop_*", "material": "PolygonCastle_GLASS", "flavor": "Atlas"},
            ],
        }}}
        output = run_report(totals, contexts)
        dead_lines = [line for line in output.splitlines() if "DEAD" in line]
        self.assertEqual(len(dead_lines), 1)
        self.assertIn("SM_Prop_*", dead_lines[0])
        self.assertIn("PolygonCastle_GLASS", dead_lines[0])
        self.assertNotIn("SM_Bld_Preset_*", dead_lines[0])


if __name__ == "__main__":
    unittest.main()
