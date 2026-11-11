# tests/test_engines.py
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from newscentral.translate import base, m2m100, nllb, opus_mt, registry

SPEC_LANGS = ["fr", "pt", "it", "de", "ca", "ar", "fa", "tr", "zh", "ja", "ko", "ru", "uk", "hi", "ur", "bn", "id"]


class FakePieces:
    """Stands in for a sentencepiece processor: pieces are words."""

    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, pieces):
        return " ".join(pieces)


class Result:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class FakeTranslator:
    """Stands in for ctranslate2.Translator: upper-cases the word tokens, keeps the target prefix."""

    def __init__(self):
        self.calls = []

    def translate_batch(self, source, target_prefix=None, beam_size=2, max_decoding_length=256):
        self.calls.append({"source": source, "target_prefix": target_prefix})
        out = []
        for i, tokens in enumerate(source):
            words = [t.upper() for t in tokens if t != "</s>" and "_" not in t]
            prefix = target_prefix[i] if target_prefix else []
            out.append(Result(list(prefix) + words + ["</s>"]))
        return out


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def touch(self, *parts):
        path = os.path.join(self.dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x")


class TestAvailability(EngineCase):
    def test_registry_names(self):
        self.assertEqual(sorted(registry.ENGINES), ["fake", "m2m100-418m", "nllb-600m", "opus-mt"])

    def test_missing_packages(self):
        with mock.patch("importlib.util.find_spec", return_value=None):
            reason = registry.check("nllb-600m", self.dir)
        self.assertIn("ctranslate2, sentencepiece", reason)
        self.assertIn("requirements-translate.txt", reason)

    def test_missing_models(self):
        with mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertIn("py tools/get_models.py nllb-600m", registry.check("nllb-600m", self.dir))
            self.assertIn("py tools/get_models.py m2m100-418m", registry.check("m2m100-418m", self.dir))
            self.assertIn("py tools/get_models.py opus-mt", registry.check("opus-mt", self.dir))
            self.touch("nllb-600m", "model.bin")
            self.assertEqual(registry.check("nllb-600m", self.dir), "")
            self.assertEqual(registry.installed(self.dir), ["nllb-600m"])

    def test_creating_an_engine_loads_nothing(self):
        engine = registry.create("nllb-600m", self.dir, threads=3)
        self.assertIsNone(engine._translator)
        self.assertEqual(engine.threads, 3)
        self.assertEqual(engine.translate([], "ar", "es"), [])

    def test_version_from_source_json(self):
        os.makedirs(os.path.join(self.dir, "nllb-600m"))
        with open(os.path.join(self.dir, "nllb-600m", "source.json"), "w", encoding="utf-8") as f:
            json.dump({"repo": "r", "revision": "0123456789abcdef"}, f)
        self.assertEqual(registry.create("nllb-600m", self.dir).version, "0123456789ab")
        self.assertEqual(registry.create("m2m100-418m", self.dir).version, "unknown")


class TestLanguages(unittest.TestCase):
    def test_spec_languages_are_covered(self):
        n = nllb.NllbEngine("unused")
        m = m2m100.M2M100Engine("unused")
        for lang in SPEC_LANGS:
            self.assertTrue(n.supports(lang, "es"), lang)
            self.assertTrue(m.supports(lang, "es"), lang)
        self.assertFalse(n.supports("es", "es"))
        self.assertFalse(m.supports("eu", "es"))       # M2M100 has no Basque
        self.assertTrue(n.supports("eu", "es"))

    def test_codes(self):
        self.assertEqual([nllb.NLLB_CODES[c] for c in ("ar", "fa", "zh", "es", "ms")],
                         ["arb_Arab", "pes_Arab", "zho_Hans", "spa_Latn", "zsm_Latn"])
        self.assertEqual(m2m100.lang_token("es"), "__es__")


class TestTokenGlue(EngineCase):
    def test_strip_special(self):
        self.assertEqual(base.strip_special(["<s>", "a", "</s>", "<pad>"]), ["a"])

    def test_nllb(self):
        engine = nllb.NllbEngine(self.dir)
        engine._sp, engine._translator = FakePieces(), FakeTranslator()
        self.assertEqual(engine.translate(["hola mundo", "otra"], "fr", "es"), ["HOLA MUNDO", "OTRA"])
        call = engine._translator.calls[0]
        self.assertEqual(call["source"][0], ["fra_Latn", "hola", "mundo", "</s>"])
        self.assertEqual(call["target_prefix"], [["spa_Latn"], ["spa_Latn"]])

    def test_m2m100(self):
        engine = m2m100.M2M100Engine(self.dir)
        engine._sp, engine._translator = FakePieces(), FakeTranslator()
        self.assertEqual(engine.translate(["bonjour"], "fr", "es"), ["BONJOUR"])
        call = engine._translator.calls[0]
        self.assertEqual(call["source"], [["__fr__", "bonjour", "</s>"]])
        self.assertEqual(call["target_prefix"], [["__es__"]])

    def test_opus_routes_and_pivot(self):
        os.makedirs(os.path.join(self.dir, "opus-mt"))
        with open(os.path.join(self.dir, "opus-mt", "routes.json"), "w", encoding="utf-8") as f:
            json.dump({"fr": ["fr-es"], "zh": ["zh-en", "en-es"]}, f)
        self.touch("opus-mt", "fr-es", "model.bin")
        self.touch("opus-mt", "zh-en", "model.bin")
        engine = opus_mt.OpusMtEngine(os.path.join(self.dir, "opus-mt"))
        self.assertTrue(engine.supports("fr", "es"))
        self.assertFalse(engine.supports("zh", "es"))            # en-es missing
        self.assertFalse(engine.supports("fr", "en"))
        self.touch("opus-mt", "en-es", "model.bin")
        self.assertTrue(engine.supports("zh", "es"))
        translators = {}
        for pair in ("fr-es", "zh-en", "en-es"):
            translators[pair] = FakeTranslator()
            engine._pairs[pair] = (translators[pair], FakePieces(), FakePieces())
        self.assertEqual(engine.translate(["ni hao"], "zh", "es"), ["NI HAO"])
        self.assertEqual(translators["zh-en"].calls[0]["source"], [["ni", "hao", "</s>"]])
        self.assertEqual(translators["en-es"].calls[0]["source"], [["NI", "HAO", "</s>"]])
        self.assertIsNone(translators["zh-en"].calls[0]["target_prefix"])
        self.assertEqual(translators["fr-es"].calls, [])


if __name__ == "__main__":
    unittest.main()
