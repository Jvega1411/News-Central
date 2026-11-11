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

    def test_lang_must_be_in_reference(self):
        langs = {"es": {"name": "Spanish", "script": "Latn", "rtl": False}}
        countries = {"ES": {"name": "Spain", "region": "Spain"}}
        ok = {"id": "a", "country_iso": "ES", "lang": "es"}
        self.assertEqual(sources.validate_sources([ok], countries, langs), [])
        errs = sources.validate_sources([dict(ok, lang="xx")], countries, langs)
        self.assertTrue(any("lang" in e for e in errs))
        errs = sources.validate_sources([{"id": "b", "country_iso": "ES"}], countries, langs)
        self.assertTrue(any("lang" in e for e in errs))

    def test_kind_vocabulary(self):
        langs = {"es": {"name": "Spanish", "script": "Latn", "rtl": False}}
        countries = {"ES": {"name": "Spain", "region": "Spain"}}
        base = {"id": "a", "country_iso": "ES", "lang": "es"}
        for kind in ("news", "analysis", "official"):
            self.assertEqual(sources.validate_sources([dict(base, kind=kind)], countries, langs), [])
        self.assertEqual(sources.validate_sources([base], countries, langs), [])   # kind is optional
        errs = sources.validate_sources([dict(base, kind="blog")], countries, langs)
        self.assertTrue(any("kind" in e for e in errs))

if __name__ == "__main__":
    unittest.main()
