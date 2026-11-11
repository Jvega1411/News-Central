# tests/test_bench.py
import contextlib
import io
import json
import os
import unittest

from newscentral.translate import bench
from newscentral.translate.fake import FakeEngine
from tests.helpers import TranslationCase, load_tool


class ArabicOnly(FakeEngine):
    def supports(self, src, tgt):
        return src == "ar"


def ticking(step=0.5):
    state = [0.0]

    def clock():
        state[0] += step
        return state[0]
    return clock


class TestSample(TranslationCase):
    def test_newest_per_language_with_both_fields(self):
        units = bench.sample_units(self.store, self.READ, per_lang=1)
        self.assertEqual([u["key"] for u in units], [
            self.ids["old_ar"] + "|title", self.ids["old_ar"] + "|summary",
            self.ids["new_ru"] + "|title", self.ids["new_ru"] + "|summary"])
        self.assertEqual(units[0]["lang"], "ar")
        self.assertEqual(units[0]["source_id"], "ahram")

    def test_items_without_summary_give_one_unit(self):
        keys = [u["key"] for u in bench.sample_units(self.store, self.READ, per_lang=15)]
        self.assertIn(self.ids["ar_no_summary"] + "|title", keys)
        self.assertNotIn(self.ids["ar_no_summary"] + "|summary", keys)
        self.assertEqual(len(keys), 5)


class TestRun(TranslationCase):
    def test_records_text_and_seconds(self):
        units = bench.sample_units(self.store, self.READ, per_lang=1)
        result = bench.run_bench(units, {"fake": FakeEngine("x")}, batch_size=16, clock=ticking(), date="2026-11-20")
        self.assertEqual(result["date"], "2026-11-20")
        self.assertEqual(result["target"], "es")
        tr = result["items"][0]["translations"]["fake"]
        self.assertTrue(tr["text"].startswith("[ar>es] "))
        self.assertEqual((tr["seconds"], tr["error"]), (0.5, ""))        # one unit per batch, 0.5 s per tick
        self.assertEqual(result["engines"]["fake"]["errors"], 0)
        self.assertEqual(result["engines"]["fake"]["seconds_per_item"], {"title": 0.5, "summary": 0.5})

    def test_unsupported_languages_are_marked(self):
        units = bench.sample_units(self.store, self.READ, per_lang=1)
        result = bench.run_bench(units, {"ar-only": ArabicOnly("x")}, clock=ticking())
        by_key = {i["key"]: i["translations"]["ar-only"] for i in result["items"]}
        self.assertEqual(by_key[self.ids["new_ru"] + "|title"]["error"], "unsupported")
        self.assertEqual(by_key[self.ids["old_ar"] + "|title"]["error"], "")


class TestSummary(unittest.TestCase):
    def bench(self):
        def item(key, lang, a, b):
            return {"key": key, "lang": lang, "translations": {"a": a, "b": b}}
        ok_a = {"text": "t", "seconds": 0.5, "error": ""}
        ok_b = {"text": "t", "seconds": 0.1, "error": ""}
        return {"engines": {"a": {}, "b": {}}, "items": [
            item("1|title", "ar", ok_a, ok_b),
            item("2|title", "zh", ok_a, ok_b),
            item("3|title", "zh", ok_a, {"text": "", "seconds": 0.1, "error": "empty output"}),
            item("4|title", "ko", ok_a, {"text": "", "seconds": None, "error": "unsupported"}),
        ]}

    def test_adequacy_first_then_speed(self):
        scores = {"1|title": {"a": {"adequacy": 4, "fluency": 4}, "b": {"adequacy": 4.04, "fluency": 3}},
                  "2|title": {"a": {"adequacy": 3, "fluency": 3}, "b": {"adequacy": 3, "fluency": 3}},
                  "3|title": {"a": {"adequacy": 3, "fluency": 3}},
                  "4|title": {"a": {"adequacy": 2, "fluency": 2}}}
        out = bench.summarize(self.bench(), scores)
        self.assertEqual(out["choice"], {"ar": "b", "ko": "a", "zh": "a"})
        self.assertEqual(out["by_lang"]["zh"]["b"]["adequacy"], 2.0)   # the error counts as 1
        self.assertNotIn("b", out["by_lang"]["ko"])                    # unsupported is not a candidate
        self.assertEqual(out["translate_engine"], {"default": "a", "ar": "b"})

    def test_compress(self):
        self.assertEqual(bench.compress({}), {})
        self.assertEqual(bench.compress({"ar": "x", "fr": "y", "zh": "y"}), {"default": "y", "ar": "x"})


class TestFiles(TranslationCase):
    def test_paths_reject_bad_dates(self):
        self.assertTrue(bench.bench_path(self.dir, "2026-11-20", "scores").endswith("translate-2026-11-20-scores.json"))
        with self.assertRaises(ValueError):
            bench.bench_path(self.dir, "../../etc")

    def test_round_trip_and_review(self):
        bench_dir = os.path.join(self.dir, "bench")
        data = {"date": "2026-11-20", "engines": {"fake": {}}, "items": [{"key": "k%d" % n} for n in range(40)]}
        bench.write_json(bench.bench_path(bench_dir, "2026-11-20"), data)
        bench.write_json(bench.bench_path(bench_dir, "2026-11-20", "scores"),
                         {"scores": {"k%d" % n: {"fake": {"adequacy": 4, "fluency": 4}} for n in range(30)}})
        self.assertEqual(bench.list_dates(bench_dir), ["2026-11-20"])
        loaded, scores, review = bench.load_round(bench_dir, "2026-11-20")
        self.assertEqual((len(loaded["items"]), len(scores), review), (40, 30, {}))
        sample = bench.review_sample(loaded, scores)
        self.assertEqual(len(sample), 25)
        self.assertEqual(sample, bench.review_sample(loaded, scores))     # stable
        self.assertTrue(set(sample) <= set(scores))
        bench.save_review(bench_dir, "2026-11-20", "k1", "fake", True, "meaning lost")
        self.assertEqual(bench.load_round(bench_dir, "2026-11-20")[2],
                         {"k1": {"fake": {"disagree": True, "note": "meaning lost"}}})


class TestTool(TranslationCase):
    def test_run_with_the_fake_engine(self):
        config = os.path.join(self.dir, "config.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": self.dir}, f)
        tool = load_tool("bench_translate")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tool.main(["run", "--config", config, "--engines", "fake", "--per-lang", "2"])
        self.assertEqual(code, 0, out.getvalue())
        dates = bench.list_dates(os.path.join(self.dir, "bench"))
        self.assertEqual(len(dates), 1)
        data, _, _ = bench.load_round(os.path.join(self.dir, "bench"), dates[0])
        self.assertEqual(len(data["items"]), 5)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(["summarize", "--config", config]), 1)    # no scores yet


if __name__ == "__main__":
    unittest.main()
