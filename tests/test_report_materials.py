import collections
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conversion
import reporting

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
        reporting.report_materials(totals, contexts)
    return stdout.getvalue()


def make_totals(materials):
    """A Totals carrying only what report_materials reads: the per-pack materials dict."""
    totals = conversion.Totals()
    totals.materials = materials
    return totals


def companion_material_entry(albedo_member, used_by=1, emission=None, normal=None):
    """One entry of totals.materials, as the worker produced it plus the CLI's bookkeeping.

    Distinct from material_entry above, which only exercises a bare albedo channel for
    binding tests: companion reporting reads the emission and normal channels too, and the
    channel filler always writes the full surface-property shape alongside them.
    """
    channels = {"albedo": {"member": albedo_member, "texture_source": albedo_member,
                           "texture": "/out/Pack/" + albedo_member, "reference": None,
                           "method": "exact"}}
    if emission:
        channels["emission"] = emission
    if normal:
        channels["normal"] = normal
    return {"channels": channels, "used_by": used_by, "sources": {"lambert1"},
            "source": "lambert1", "color": [1.0, 1.0, 1.0], "alpha": 1.0,
            "emission_color": [0.0, 0.0, 0.0], "emission_strength": 1.0,
            "roughness": 0.5528, "metallic": 0.0, "normal_strength": 1.0}


class FilledSurvivesTheMerge(unittest.TestCase):
    """Regression test for the race the previous fix repaired.

    A flavor fill renames itself after its texture and can merge with an identically named
    record that resolved normally; whichever arrived first wins the merge, so
    totals.materials can hold a record with no `method: "flavor"` marker even though the
    fill genuinely happened. report_materials must read totals.filled, counted per model
    before that merge, rather than re-deriving "filled" from the merged dict.
    """

    def setUp(self):
        self.totals = conversion.Totals()
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
        totals = conversion.Totals()
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


class UntexturedCountExcludesFills(unittest.TestCase):
    """Regression test for the fill exclusion in the untextured count.

    A flavor fill leaves `reference` empty, since nothing in the FBX asked for the texture
    it received, but it does carry a `texture`. Without excluding that case too, a material
    the pack just fixed by filling it would still be counted as untextured, undoing the
    point of filling it.
    """

    def test_filled_material_is_not_counted_as_untextured(self):
        totals = conversion.Totals()
        totals.materials = {PACK: {
            "Wall_Brick_01": material_entry("Wall_Brick_01", "flavor", None)}}
        contexts = {PACK: {"materials": {"sets": {}, "bind": []}}}
        output = run_report(totals, contexts)
        self.assertIn("0 untextured", output)


class CompanionReporting(unittest.TestCase):

    # An absolute source root, so the fixtures respect the shape material_flavors.relative
    # expects: the textures index holds absolute paths, and relative() converts them against
    # this root into the pack-relative POSIX strings a context otherwise uses throughout.
    SOURCE_ROOT = "C:/Source/Pack"

    def texture_path(self, name):
        """An absolute path under SOURCE_ROOT, the shape the textures index holds."""
        return f"{self.SOURCE_ROOT}/{name}"

    def report(self, materials, companions, judge=True, textures=None, source_root=None,
               sets=None):
        totals = make_totals(materials={"Pack": materials})
        contexts = {"Pack": {"materials": {"sets": sets or {}, "bind": [],
                                           "companions": companions},
                             "textures": textures or [],
                             "source_root": source_root or self.SOURCE_ROOT}}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            reporting.report_materials(totals, contexts, judge_bindings=judge)
        return buffer.getvalue()

    def test_a_companion_that_fired_is_named_with_its_count(self):
        materials = {"Atlas_01_A_Emissive_01": companion_material_entry(
            "Textures/Atlas_01_A.png", used_by=12,
            emission={"member": "Textures/Emissive_01.png", "method": "companion",
                      "texture": "/out/Pack/Textures/Emissive_01.png"})}
        output = self.report(materials, {"Textures/Atlas_01_A.png":
                                         {"emission": "Textures/Emissive_01.png"}})
        self.assertIn("companion", output)
        self.assertIn("Emissive_01.png", output)
        self.assertIn("(12 models)", output)

    def test_a_companion_nothing_wore_is_reported_dead(self):
        # The failure mode this exists to catch: an atlas gets renamed in a pack update and
        # the entry keeps resolving against the texture index while no material wears it.
        materials = {"Atlas_02_A": companion_material_entry("Textures/Atlas_02_A.png", used_by=3)}
        output = self.report(materials, {"Textures/Atlas_01_A.png":
                                         {"emission": "Textures/Emissive_01.png"}})
        self.assertIn("DEAD", output)
        self.assertIn("Textures/Atlas_01_A.png", output)

    def test_dead_is_not_claimed_on_a_run_that_could_not_observe_it(self):
        materials = {"Atlas_02_A": companion_material_entry("Textures/Atlas_02_A.png", used_by=3)}
        output = self.report(materials, {"Textures/Atlas_01_A.png":
                                         {"emission": "Textures/Emissive_01.png"}},
                             judge=False)
        self.assertNotIn("DEAD", output)

    def test_an_unclaimed_emissive_texture_is_a_candidate(self):
        # Nothing in the pack references or claims this map, so it belongs on the
        # authoring worklist.
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        output = self.report(materials, {},
                             textures=[self.texture_path("Textures/Roof_Emissive_01.png")])
        self.assertIn("candidates 1 unbound companion map(s)", output)
        self.assertIn("Roof_Emissive_01.png", output)

    def test_a_texture_bound_by_a_non_albedo_channel_is_excluded(self):
        # The exclusion gathers members across albedo, emission, normal and alpha; a bug
        # that only checked albedo would still let a normal-bound texture through here.
        materials = {"Wall_01": companion_material_entry(
            "Textures/Wall_01.png",
            normal={"member": "Textures/Wall_01_Normal.png", "method": "exact",
                    "texture": "/out/Pack/Textures/Wall_01_Normal.png"})}
        output = self.report(materials, {}, textures=[
            self.texture_path("Textures/Wall_01_Normal.png"),
            self.texture_path("Textures/Roof_Emissive_01.png")])
        self.assertIn("candidates 1 unbound companion map(s)", output)
        self.assertIn("Roof_Emissive_01.png", output)
        self.assertNotIn("Wall_01_Normal.png", output)

    def test_a_texture_claimed_by_a_companion_entry_is_excluded(self):
        # Claimed means the texture appears as a companion VALUE, regardless of whether any
        # material actually wears it yet.
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        companions = {"Textures/Atlas_02_A.png": {"emission": "Textures/Emissive_02.png"}}
        output = self.report(materials, companions, textures=[
            self.texture_path("Textures/Emissive_02.png"),
            self.texture_path("Textures/Emissive_03.png")])
        self.assertIn("candidates 1 unbound companion map(s)", output)
        self.assertIn("Emissive_03.png", output)
        self.assertNotIn("Emissive_02.png", output)

    def test_a_texture_named_like_neither_map_is_never_a_candidate(self):
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        output = self.report(materials, {}, textures=[
            self.texture_path("Textures/PolygonCasino_Texture_01_A.png")])
        self.assertNotIn("candidates", output)

    def test_a_companion_on_an_unworn_flavor_sibling_is_not_reported_dead(self):
        # Nothing wears Atlas_01_B directly, but Atlas_01_A does, and both are members of
        # the same flavor set. write_manifests will generate a sibling record wearing
        # Atlas_01_B once it runs, and that sibling gets its own member's companion, so the
        # entry declared here already does real work despite reaching no observed material.
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        companions = {"Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01.png"}}
        output = self.report(materials, companions, sets=sets)
        self.assertNotIn("DEAD", output)

    def test_a_companion_outside_any_drawn_flavor_set_is_still_reported_dead(self):
        # An unrelated flavor set exists in the pack and IS drawn from, but the companion's
        # own texture is not one of its members, so reachability through a sibling never
        # applies to it. A fix that grants reachability merely because some set was drawn
        # from, without checking set membership, would wrongly clear this entry.
        materials = {"Atlas_02_A": companion_material_entry("Textures/Atlas_02_A.png"),
                    "Wall_01": companion_material_entry("Textures/Wall_01.png")}
        sets = {"Wall": {"members": ["Textures/Wall_01.png", "Textures/Wall_02.png"],
                         "default": "Textures/Wall_01.png"}}
        companions = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01.png"}}
        output = self.report(materials, companions, sets=sets)
        self.assertIn("DEAD", output)
        self.assertIn("Textures/Atlas_01_A.png", output)

    def test_a_companion_on_an_undrawn_flavor_set_is_still_reported_dead(self):
        # The set exists and the companion is one of its own members, but no observed
        # albedo belongs to the set, so write_manifests generates no sibling for it either.
        # Reachability has to check the set was drawn from, not just that it exists.
        materials = {"Wall_01": companion_material_entry("Textures/Wall_01.png")}
        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        companions = {"Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01.png"}}
        output = self.report(materials, companions, sets=sets)
        self.assertIn("DEAD", output)
        self.assertIn("Textures/Atlas_01_B.png", output)

    def test_dead_is_not_claimed_for_an_undrawn_flavor_set_on_an_unjudged_run(self):
        # Same genuinely-inert entry as the previous test, but judge_bindings is False: the
        # gate must still suppress DEAD entirely, proving the new reachability path stays
        # inside the existing gate rather than printing on its own.
        materials = {"Wall_01": companion_material_entry("Textures/Wall_01.png")}
        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        companions = {"Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01.png"}}
        output = self.report(materials, companions, sets=sets, judge=False)
        self.assertNotIn("DEAD", output)

    def test_a_sibling_only_companion_is_reported_and_distinct_from_a_worn_one(self):
        # Atlas_01_A is worn directly. Atlas_01_B is in the same set but nothing wears it,
        # though write_manifests still generates a sibling taking its own companion. Both
        # get a line, and only the worn one carries a model count.
        materials = {"Atlas_01_A_Emissive": companion_material_entry(
            "Textures/Atlas_01_A.png", used_by=5,
            emission={"member": "Textures/Emissive_01_A.png", "method": "companion",
                      "texture": "/out/Pack/Textures/Emissive_01_A.png"})}
        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        companions = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01_A.png"},
                      "Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01_B.png"}}
        output = self.report(materials, companions, sets=sets)
        companion_lines = [line for line in output.splitlines()
                           if line.strip().startswith("companion")]
        sibling_lines = [line for line in output.splitlines()
                         if line.strip().startswith("sibling")]
        self.assertEqual(len(companion_lines), 1)
        self.assertIn("Atlas_01_A.png", companion_lines[0])
        self.assertIn("(5 models)", companion_lines[0])
        self.assertEqual(len(sibling_lines), 1)
        self.assertIn("Atlas_01_B.png", sibling_lines[0])
        self.assertIn("Emissive_01_B.png", sibling_lines[0])
        self.assertNotIn("models)", sibling_lines[0])
        self.assertNotIn("DEAD", output)

    def test_a_worn_member_does_not_also_print_its_own_sibling_line(self):
        # Atlas_01_A is both directly worn and a member of the flavor set it belongs to, so
        # a `reachable` set that forgot to subtract `worn` would print it twice: once as
        # "companion" with its real count, once as "sibling" wrongly claiming no model
        # wears it. Only the companion line is true here.
        materials = {"Atlas_01_A_Emissive": companion_material_entry(
            "Textures/Atlas_01_A.png", used_by=7,
            emission={"member": "Textures/Emissive_01_A.png", "method": "companion",
                      "texture": "/out/Pack/Textures/Emissive_01_A.png"})}
        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        companions = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01_A.png"}}
        output = self.report(materials, companions, sets=sets)
        self.assertIn("(7 models)", output)
        self.assertNotIn("sibling", output)

    def test_a_companion_neither_worn_nor_sibling_reachable_reports_dead_not_sibling(self):
        # Atlas_02_A wears no companion, belongs to no flavor set, and the declared
        # companion sits on a different atlas entirely. Nothing can reach it, so it must
        # still print DEAD, and specifically must not print as a sibling line, which would
        # wrongly claim write_manifests can still reach it through a generated sibling.
        materials = {"Atlas_02_A": companion_material_entry("Textures/Atlas_02_A.png", used_by=3)}
        companions = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01_A.png"}}
        output = self.report(materials, companions)
        self.assertIn("DEAD", output)
        self.assertIn("Textures/Atlas_01_A.png", output)
        self.assertNotIn("sibling", output)

    def test_judge_bindings_false_suppresses_dead_but_not_the_sibling_line(self):
        # Atlas_01_B is sibling-reachable; Atlas_02_A is reachable by nothing. Without
        # judging, the DEAD verdict on Atlas_02_A stays suppressed while the sibling line
        # still prints: that is a fact about what this run converted, not an absence claim.
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        sets = {"Atlas": {"members": ["Textures/Atlas_01_A.png", "Textures/Atlas_01_B.png"],
                          "default": "Textures/Atlas_01_A.png"}}
        companions = {"Textures/Atlas_01_B.png": {"emission": "Textures/Emissive_01_B.png"},
                      "Textures/Atlas_02_A.png": {"emission": "Textures/Emissive_02_A.png"}}
        output = self.report(materials, companions, sets=sets, judge=False)
        self.assertNotIn("DEAD", output)
        self.assertIn("sibling", output)
        self.assertIn("Atlas_01_B.png", output)

    def test_more_than_six_candidates_are_truncated_with_a_correct_remainder(self):
        # Nine unbound, unclaimed emissive maps: the line must name the first six sorted by
        # name and report the remaining three as "and 3 more", not any other split.
        letters = "ABCDEFGHI"
        names = [f"Textures/Wall_{letter}_Emissive.png" for letter in letters]
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        output = self.report(materials, {},
                             textures=[self.texture_path(name) for name in names])
        self.assertIn("candidates 9 unbound companion map(s)", output)
        for letter in "ABCDEF":
            self.assertIn(f"Wall_{letter}_Emissive.png", output)
        for letter in "GHI":
            self.assertNotIn(f"Wall_{letter}_Emissive.png", output)
        self.assertIn("and 3 more", output)


class UnjudgedRunNotice(unittest.TestCase):
    """Regression test for Minor 1: the notice must name whichever table went unjudged.

    Horror Carnival, Casino, War and City declare companions and no `bind` entries at
    all, so a run that cannot judge health, `--untextured keep` or an incremental run,
    must still say so for them. Before this fix the notice only fired under
    `elif bindings:`, so a companion-only pack stayed silent about its skipped DEAD
    check.
    """

    def report(self, materials, bindings, companions, judge):
        totals = make_totals(materials={PACK: materials})
        contexts = {PACK: {"materials": {
            "sets": {}, "bind": bindings, "companions": companions}}}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            reporting.report_materials(totals, contexts, judge_bindings=judge)
        return buffer.getvalue()

    def test_companion_only_pack_is_named_when_unjudged(self):
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        companions = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01.png"}}
        output = self.report(materials, [], companions, judge=False)
        self.assertIn("companion health not assessed this run", output)
        self.assertNotIn("binding health not assessed this run", output)

    def test_binding_only_pack_is_named_when_unjudged(self):
        materials = {"Wall_Evil_01": material_entry("Wall_Evil_01", "exact", "Wall_Evil_01.png")}
        bindings = [{"model": "*", "material": "Wall*", "flavor": "Wall"}]
        output = self.report(materials, bindings, {}, judge=False)
        self.assertIn("binding health not assessed this run", output)
        self.assertNotIn("companion health not assessed this run", output)

    def test_both_tables_are_named_together_when_unjudged(self):
        materials = {"Atlas_01_A": companion_material_entry("Textures/Atlas_01_A.png")}
        bindings = [{"model": "*", "material": "Wall*", "flavor": "Wall"}]
        companions = {"Textures/Atlas_01_A.png": {"emission": "Textures/Emissive_01.png"}}
        output = self.report(materials, bindings, companions, judge=False)
        self.assertIn("binding and companion health not assessed this run", output)

    def test_neither_table_prints_no_notice(self):
        materials = {"Wall_Evil_01": material_entry("Wall_Evil_01", "exact", "Wall_Evil_01.png")}
        output = self.report(materials, [], {}, judge=False)
        self.assertNotIn("not assessed", output)


class CompanionNamed(unittest.TestCase):
    """companion_named decides the authoring worklist, so its name recognition is load-bearing:
    a false negative here is a map nobody ever notices is missing.
    """

    def test_recognizes_the_emissive_and_normal_spellings_the_packs_actually_ship(self):
        companion_named_examples = (
            "Wall_Brick_01_Normals",
            "Sand_02_Normal",
            "PolygonCasino_Emissive_01_A",
            "HotelWall_Emissive",
            "Dungeons_Texture_FloorTiles_Normal",
        )
        for name in companion_named_examples:
            with self.subTest(name=name):
                self.assertTrue(reporting.companion_named(name))

    def test_recognizes_the_misspellings_synty_actually_shipped(self):
        # Every one of these is a real shipped filename: a doubled m, a doubled m with a
        # doubled s, a doubled m against the "-sion" ending, and a vowel-less "Normal".
        misspelled_companion_named_examples = (
            "Emmisive_01",
            "Lavawave_Hot_Inverted_Emmissive",
            "PolygonBattleRoyale_Spotlight_01_Emmision",
            "PolygonFantasyKingdom_01_Emmisive",
            "PolygonGangWarfare_Leaves_Nrml",
            "Texture_Emmisive",
        )
        for name in misspelled_companion_named_examples:
            with self.subTest(name=name):
                self.assertTrue(reporting.companion_named(name))

    def test_rejects_an_ordinary_atlas_name(self):
        ordinary_names = ("PolygonCasino_Texture_01_A", "Wall_Brick_01")
        for name in ordinary_names:
            with self.subTest(name=name):
                self.assertFalse(reporting.companion_named(name))


if __name__ == "__main__":
    unittest.main()
