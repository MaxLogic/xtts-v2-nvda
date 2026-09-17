"""Reference order determines the prefix available for GPT style conditioning."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2/_reference_coverage.py"
spec = importlib.util.spec_from_file_location("reference_coverage", PATH)
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class ReferenceCoverageTests(unittest.TestCase):
    def test_default_prefix_can_exclude_later_speakers_style(self):
        self.assertEqual(coverage.style_seconds([10, 8], 30, 6, 6), [6, 0])
        self.assertEqual(coverage.style_seconds([4, 8], 30, 6, 6), [4, 2])

    def test_per_recording_cap_and_skipped_short_tail(self):
        self.assertEqual(coverage.style_seconds([60, 8], 3, 6, 6), [3, 3])
        values = coverage.style_seconds([6, .2], 30, 12, 6)
        self.assertEqual(values, [6, 0])

    def test_unknown_duration_does_not_claim_later_exclusion(self):
        self.assertEqual(coverage.style_seconds([None, 9], 30, 6, 6), [None, None])

    def test_balanced_budget_reaches_every_recording_regardless_of_order(self):
        # Fish case: short fragment, long chapter, medium recording, 12 s budget.
        values = coverage.balanced_style_seconds([2.46, 1149, 53.34], 30, 12)
        self.assertAlmostEqual(values[0], 2.46)
        self.assertAlmostEqual(values[1], 4.77)
        self.assertAlmostEqual(values[2], 4.77)
        self.assertEqual(coverage.balanced_style_seconds([53.34, 1149, 2.46], 30, 12), list(reversed(values)))

    def test_balanced_budget_never_exceeds_available_audio(self):
        self.assertEqual(coverage.balanced_style_seconds([1, 2], 30, 12), [1, 2])
        self.assertEqual(coverage.balanced_style_seconds([60, 60], 3, 12), [3, 3])
        self.assertEqual(coverage.balanced_style_seconds([None, 9], 30, 6), [None, None])

    def test_centered_window(self):
        self.assertEqual(coverage.centered_window(10, 4), (3, 7))
        self.assertEqual(coverage.centered_window(2, 4), (0, 2))
