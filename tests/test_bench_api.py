# tests/test_bench_api.py
import os
import unittest

from newscentral.translate import bench
from tests.helpers import ApiCase

DATE = "2026-11-20"


class TestBenchApi(ApiCase):
    def setUp(self):
        super().setUp()
        self.app.config["data_dir"] = self.dir
        self.bench_dir = os.path.join(self.dir, "bench")
        items = [{"key": "a1|title", "item_id": "a1", "source_id": "ahram", "lang": "ar", "field": "title",
                  "text": "خبر", "translations": {
                      "x": {"text": "Noticia", "seconds": 0.2, "error": ""},
                      "y": {"text": "", "seconds": 0.1, "error": "empty output"}}},
                 {"key": "r1|title", "item_id": "r1", "source_id": "meduza", "lang": "ru", "field": "title",
                  "text": "Новости", "translations": {
                      "x": {"text": "Noticias", "seconds": 0.2, "error": ""},
                      "y": {"text": "Novedades", "seconds": 0.1, "error": ""}}}]
        bench.write_json(bench.bench_path(self.bench_dir, DATE),
                         {"date": DATE, "machine": "test", "engines": {"x": {}, "y": {}}, "items": items})
        bench.write_json(bench.bench_path(self.bench_dir, DATE, "scores"),
                         {"scores": {"a1|title": {"x": {"adequacy": 5, "fluency": 4, "note": ""}}}})

    def test_payload(self):
        status, body = self.get("/api/bench/translate")
        self.assertEqual(status, 200)
        self.assertEqual((body["dates"], body["date"], body["engines"]), ([DATE], DATE, ["x", "y"]))
        self.assertEqual(body["sample"], ["a1|title"])
        self.assertTrue(body["has_scores"])
        first = body["items"][0]
        self.assertEqual(first["scores"]["x"]["adequacy"], 5)
        self.assertEqual(first["review"], {})

    def test_no_benchmark_yet(self):
        os.remove(bench.bench_path(self.bench_dir, DATE))
        _, body = self.get("/api/bench/translate")
        self.assertEqual((body["dates"], body["date"], body["items"]), ([], None, []))

    def test_unknown_date(self):
        self.assertEqual(self.get("/api/bench/translate?date=2020-01-01")[0], 400)
        self.assertEqual(self.get("/api/bench/translate?date=..%2F..%2Fx")[0], 400)

    def test_review_is_saved(self):
        status, body = self.post("/api/bench/review", {"date": DATE, "key": "a1|title", "engine": "x",
                                                       "disagree": True, "note": "too literal"})
        self.assertEqual((status, body), (200, {"ok": True}))
        _, payload = self.get("/api/bench/translate")
        self.assertEqual(payload["items"][0]["review"], {"x": {"disagree": True, "note": "too literal"}})

    def test_bad_reviews(self):
        for body in ({"date": DATE, "key": "nope", "engine": "x"},
                     {"date": DATE, "key": "a1|title", "engine": "z"},
                     {"date": "../x", "key": "a1|title", "engine": "x"}):
            self.assertEqual(self.post("/api/bench/review", body)[0], 400, body)
        self.assertEqual(self.post("/api/bench/review", {"date": DATE}, header=False)[0], 403)

    def test_page(self):
        status, html = self.get("/bench/translate")
        self.assertEqual(status, 200)
        self.assertIn('src="/js/bench.js"', html)
        self.assertEqual(self.get("/js/bench.js")[0], 200)


if __name__ == "__main__":
    unittest.main()
