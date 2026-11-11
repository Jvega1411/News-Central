import unittest

from newscentral import stats

I = 900  # 15 minutes


class TestGaps(unittest.TestCase):
    def test_steady_collection_has_no_gaps(self):
        starts = [i * I for i in range(0, 11)]
        out = stats.gaps(starts, I, since=0, now=10 * I)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["longest"], 0)
        self.assertEqual(out["uptime"], 1.0)

    def test_exactly_twice_the_interval_is_not_a_gap(self):
        out = stats.gaps([0, 2 * I], I, since=0, now=2 * I)
        self.assertEqual(out["gaps"], [])

    def test_gap_between_runs(self):
        out = stats.gaps([0, I, 5 * I, 6 * I], I, since=0, now=6 * I)
        self.assertEqual(out["gaps"], [{"from": I, "to": 5 * I, "seconds": 4 * I}])
        self.assertEqual(out["longest"], 4 * I)
        self.assertAlmostEqual(out["uptime"], 1 - (3 * I) / (6 * I))

    def test_gap_at_the_end_counts(self):
        out = stats.gaps([0, I], I, since=0, now=10 * I)
        self.assertEqual(out["gaps"], [{"from": I, "to": 10 * I, "seconds": 9 * I}])

    def test_window_starts_at_the_first_run_ever(self):
        # collection began at t=1000*I; the window must not count the time before it
        starts = [1000 * I, 1001 * I, 1002 * I]
        out = stats.gaps(starts, I, since=0, now=1002 * I)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["uptime"], 1.0)

    def test_older_run_means_the_window_starts_at_since(self):
        # a run before the window proves collection had begun, so silence inside the window is a gap
        out = stats.gaps([0, 10 * I], I, since=5 * I, now=10 * I)
        self.assertEqual(out["gaps"], [{"from": 5 * I, "to": 10 * I, "seconds": 5 * I}])

    def test_no_runs(self):
        out = stats.gaps([], I, since=0, now=100)
        self.assertEqual(out, {"gaps": [], "longest": 0, "uptime": None})


class TestVolume(unittest.TestCase):
    def test_formats_days_as_utc_dates(self):
        rows = [(20767, "es", 120, 30000), (20767, "ar", 40, 9000), (20766, "", 3, None)]
        self.assertEqual(stats.volume(rows), [
            {"day": "2026-11-10", "lang": "es", "items": 120, "chars": 30000},
            {"day": "2026-11-10", "lang": "ar", "items": 40, "chars": 9000},
            {"day": "2026-11-09", "lang": "", "items": 3, "chars": 0},
        ])


if __name__ == "__main__":
    unittest.main()
