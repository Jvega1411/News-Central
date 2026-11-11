# tests/test_press_freedom.py
import unittest
from newscentral import sources

class TestPressFreedom(unittest.TestCase):
    def test_loads_and_is_large(self):
        pf = sources.load_press_freedom()
        self.assertGreater(len(pf), 100)
        self.assertIn("NO", pf)

    def test_keys_are_upper_alpha2(self):
        for k in sources.load_press_freedom():
            self.assertEqual(len(k), 2)
            self.assertEqual(k, k.upper())
            self.assertTrue(k.isalpha())

    def test_values_are_scores(self):
        for k, v in sources.load_press_freedom().items():
            self.assertIsInstance(v, (int, float), k)
            self.assertGreaterEqual(v, 0, k)
            self.assertLessEqual(v, 100, k)

    def test_missing_file_gives_empty(self):
        old_file, old_cache = sources.PRESS_FREEDOM_FILE, sources._PRESS_FREEDOM
        try:
            sources.PRESS_FREEDOM_FILE = "does/not/exist.json"
            sources._PRESS_FREEDOM = None
            self.assertEqual(sources.load_press_freedom(), {})
        finally:
            sources.PRESS_FREEDOM_FILE = old_file
            sources._PRESS_FREEDOM = old_cache

if __name__ == "__main__":
    unittest.main()
