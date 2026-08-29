import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synty_convert

ASCII_FBX = "RuntimeError: Error: ASCII FBX files are not supported"


def run(totals):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        synty_convert.report_failures(totals)
    return stdout.getvalue()


def failure(pack, name, reason=ASCII_FBX, quote_path=True):
    """A totals.failures record, shaped like the one convert_all appends.

    Blender quotes the offending path inside the message itself, doubling its backslashes
    on the way through the worker's traceback; quote_path reproduces that faithfully.
    """
    src = f"C:\\packs\\{pack}\\Models\\{name}"
    message = f"{reason} '{src.replace(chr(92), chr(92) * 2)}'" if quote_path else reason
    return (src, pack, [message])


class FailuresNameTheirPackAndReason(unittest.TestCase):
    """A model that failed to convert is only actionable if you can find it and know why.

    Nine packs ship a file called SM_Prop_Barrel_01.fbx, so a bare basename does not say
    which one failed, and the scan path printed no reason at all: it calls report_materials
    and exits without ever reaching report(), where the failure block lived.
    """

    def setUp(self):
        self.totals = synty_convert.Totals()

    def test_nothing_is_printed_when_nothing_failed(self):
        self.assertEqual(run(self.totals), "")

    def test_failure_names_its_pack_and_carries_the_reason(self):
        self.totals.failures = [failure("Horror_Carnival", "SM_Prop_Barrel_01.fbx")]
        output = run(self.totals)
        self.assertIn("1 failure(s)", output)
        self.assertIn("Horror_Carnival/SM_Prop_Barrel_01.fbx", output)
        self.assertIn("ASCII FBX files are not supported", output)

    def test_same_basename_in_two_packs_stays_distinguishable(self):
        self.totals.failures = [failure("PackA", "SM_Prop_Barrel_01.fbx"),
                                failure("PackB", "SM_Prop_Barrel_01.fbx")]
        lines = [line for line in run(self.totals).splitlines() if "Barrel" in line]
        self.assertEqual(sorted(line.strip() for line in lines),
                         ["PackA/SM_Prop_Barrel_01.fbx", "PackB/SM_Prop_Barrel_01.fbx"])

    def test_a_failure_with_no_pack_still_prints(self):
        # run_worker reports a whole batch dying with no pack attributed to it.
        self.totals.failures = [("<worker batch of 40>", "", ["Blender exited with code 1"])]
        output = run(self.totals)
        self.assertIn("<worker batch of 40>", output)
        self.assertIn("Blender exited with code 1", output)


class FailuresGroupUnderOneReason(unittest.TestCase):
    """One fault hitting twenty models must not push a different fault out of the report.

    The listing is capped, so a flat list of twenty failures sharing one reason would hide a
    lone unrelated crash entirely. Grouping by reason keeps the rare one on screen, and matches
    how audit.py already presents its failures.
    """

    def setUp(self):
        self.totals = synty_convert.Totals()

    def test_one_reason_over_many_models_collapses_to_one_heading(self):
        self.totals.failures = [failure("Horror_Carnival", f"SM_Prop_Plushie_{i:02d}.fbx")
                                for i in range(18)]
        output = run(self.totals)
        self.assertIn("18 failure(s)", output)
        self.assertEqual(output.count(ASCII_FBX), 1)
        self.assertIn(f"{ASCII_FBX}: 18", output)
        self.assertEqual(len([line for line in output.splitlines() if ".fbx" in line]), 5)
        self.assertIn("... and 13 more", output)

    def test_a_rare_reason_survives_beside_a_common_one(self):
        self.totals.failures = (
            [failure("Horror_Carnival", f"SM_Prop_Plushie_{i:02d}.fbx") for i in range(18)]
            + [failure("PackB", "SM_Odd_01.fbx", reason="RuntimeError: something else")])
        output = run(self.totals)
        self.assertIn("RuntimeError: something else: 1", output)
        self.assertIn("PackB/SM_Odd_01.fbx", output)
        # The commoner fault is listed first, so a truncated read still shows the big one.
        self.assertLess(output.index(ASCII_FBX), output.index("something else"))


class FailureReasonDropsTheModelsOwnPath(unittest.TestCase):
    """Grouping only works if the same fault produces the same string for every model."""

    def test_the_quoted_path_is_removed(self):
        src, _, message = failure("PackA", "SM_Prop_Barrel_01.fbx")
        self.assertEqual(synty_convert.failure_reason(src, message), ASCII_FBX)

    def test_a_message_without_a_path_is_unchanged(self):
        src, _, message = failure("PackA", "SM_Prop_Barrel_01.fbx", quote_path=False)
        self.assertEqual(synty_convert.failure_reason(src, message), ASCII_FBX)

    def test_an_unrelated_path_is_left_alone(self):
        # Only the job's own path is stripped; a path naming something else is evidence.
        message = ["RuntimeError: cannot open 'C:\\\\packs\\\\PackB\\\\Textures\\\\atlas.png'"]
        self.assertIn("atlas.png", synty_convert.failure_reason("C:\\packs\\PackA\\a.fbx", message))

    def test_a_message_that_was_only_a_path_does_not_become_empty(self):
        src, _, _ = failure("PackA", "SM_Prop_Barrel_01.fbx")
        self.assertEqual(synty_convert.failure_reason(src, [src]), "unknown error")


class RepairsAreReported(unittest.TestCase):
    """A repair changes what was imported, so a silent one is a repair nobody audits."""

    def report(self, totals):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            synty_convert.report(totals, 1.0)
        return stdout.getvalue()

    def test_repairs_are_counted_on_their_own_line(self):
        self.assertIn("Repaired  21 ASCII FBX",
                      self.report(synty_convert.Totals(converted=21, repaired=21)))

    def test_a_run_with_no_repairs_says_nothing(self):
        self.assertNotIn("Repaired", self.report(synty_convert.Totals(converted=21)))


if __name__ == "__main__":
    unittest.main()
