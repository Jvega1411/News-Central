# tests/test_languages.py
import unittest

from newscentral import sources


class TestLanguages(unittest.TestCase):
    def test_reference_shape(self):
        langs = sources.load_languages()
        self.assertIn("es", langs)
        for code, meta in langs.items():
            self.assertEqual(code, code.lower())
            self.assertEqual(len(code), 2)
            self.assertEqual(set(meta), {"name", "script", "rtl"})
            self.assertEqual(len(meta["script"]), 4)
            self.assertIsInstance(meta["rtl"], bool)

    def test_rtl_languages(self):
        langs = sources.load_languages()
        self.assertEqual(sorted(c for c, m in langs.items() if m["rtl"]), ["ar", "fa", "he", "ur"])

    def test_latin_script(self):
        self.assertTrue(sources.latin_script("es"))
        self.assertTrue(sources.latin_script("vi"))
        self.assertFalse(sources.latin_script("ru"))
        self.assertFalse(sources.latin_script("zh"))
        self.assertTrue(sources.latin_script(None))      # unknown: keep the old lenient behaviour
        self.assertTrue(sources.latin_script("xx"))

    def test_every_real_source_validates(self):
        errs = sources.validate_sources(sources.load_sources(), sources.load_countries(),
                                        sources.load_languages())
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
