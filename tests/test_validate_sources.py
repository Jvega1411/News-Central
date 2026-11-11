import unittest
from newscentral import sources

COUNTRIES = {"ES": {"name": "Spain", "region": "Spain"},
             "LV": {"name": "Latvia", "region": "Europe & EU"},
             "RU": {"name": "Russia", "region": "Eurasia"}}

class TestValidate(unittest.TestCase):
    def test_clean_source_passes(self):
        src = [{"id": "a", "country_iso": "ES", "region": "Spain"}]
        self.assertEqual(sources.validate_sources(src, COUNTRIES), [])

    def test_free_text_country_rejected(self):
        src = [{"id": "a", "country": "Spain", "region": "Spain"}]
        errs = sources.validate_sources(src, COUNTRIES)
        self.assertTrue(any("country" in e for e in errs))

    def test_unknown_iso_rejected(self):
        src = [{"id": "a", "country_iso": "ZZ", "region": "x"}]
        self.assertTrue(sources.validate_sources(src, COUNTRIES))

    def test_scope_vocab_enforced(self):
        src = [{"id": "a", "country_iso": None, "scope": "Misc", "region": "x"}]
        self.assertTrue(sources.validate_sources(src, COUNTRIES))

    def test_valid_scope_passes(self):
        src = [{"id": "a", "country_iso": None, "scope": "Global", "region": "Global"}]
        self.assertEqual(sources.validate_sources(src, COUNTRIES), [])

    def test_exile_requires_covers(self):
        src = [{"id": "a", "country_iso": "LV", "exile": True, "region": "Eurasia"}]
        self.assertTrue(sources.validate_sources(src, COUNTRIES))

    def test_exile_with_valid_covers_passes(self):
        src = [{"id": "a", "country_iso": "LV", "exile": True, "covers": "RU", "region": "Eurasia"}]
        self.assertEqual(sources.validate_sources(src, COUNTRIES), [])

if __name__ == "__main__":
    unittest.main()
