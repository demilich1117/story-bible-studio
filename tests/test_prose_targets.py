import copy
import unittest

import support  # Add core scripts to the import path.
from studio_output import prose_metrics


class ProseTargetsTest(unittest.TestCase):
    def test_tolerance_boundaries_and_counts(self):
        for low, high, accepted, warned in (
            (1000, 1200, (900, 950, 1100, 1250, 1320), (899, 1321)),
            (150, 350, (100, 149, 351, 400), (99, 401)),
            (1000, None, (900, 2000), (899,)),
            (None, 350, (0, 400), (401,)),
            (None, None, (0, 5000), ()),
        ):
            snapshot = {"prose": {"min_chars": low, "max_chars": high}}
            original = copy.deepcopy(snapshot)
            for count in accepted + warned:
                with self.subTest(low=low, high=high, count=count):
                    result = prose_metrics("字" * count + " \n", snapshot)
                    self.assertEqual(count, result["prose_chars"])
                    self.assertEqual(count, result["prose_actual_chars"])
                    self.assertEqual(count in warned, "prose_warning" in result)
                    self.assertEqual(original, snapshot)

    def test_bilingual_tolerance_uses_reading_count(self):
        result = prose_metrics("“" + "a" * 600 + "”（" + "字" * 100 + "）", {
            "prose": {"min_chars": 150, "max_chars": 350},
            "bilingual": {"enabled": True},
        })
        self.assertEqual(100, result["prose_chars"])
        self.assertEqual(704, result["prose_actual_chars"])
        self.assertNotIn("prose_warning", result)
