# tests/test_countries.py
import unittest
from newscentral import sources

class TestCountries(unittest.TestCase):
    def test_loads_and_has_spain(self):
        c = sources.load_countries()
        self.assertIn("ES", c)
        self.assertEqual(c["ES"]["name"], "Spain")
        self.assertIn("region", c["ES"])

    def test_keys_are_upper_alpha2(self):
        c = sources.load_countries()
        for k in c:
            self.assertEqual(k, k.upper())
            self.assertEqual(len(k), 2)

    def test_regions_are_in_vocabulary(self):
        c = sources.load_countries()
        allowed = {"Spain", "Europe & EU", "North America", "Latin America",
                   "Africa", "Middle East & N. Africa", "East Asia", "South Asia",
                   "Southeast Asia", "Eurasia", "Oceania", "Global"}
        for k, v in c.items():
            self.assertIn(v["region"], allowed, "%s has bad region %r" % (k, v["region"]))

if __name__ == "__main__":
    unittest.main()
