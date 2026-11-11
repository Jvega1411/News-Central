import unittest

from tests.helpers import ApiCase


class TestApi(ApiCase):
    def test_status(self):
        status, body = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["running"], False)

    def test_items_returns_stored_item(self):
        self.add("elpais", "Titular", "https://example.com/a", "Resumen")
        status, body = self.get("/api/items?since=0")
        self.assertEqual(status, 200)
        self.assertEqual([i["title"] for i in body["items"]], ["Titular"])
        self.assertEqual(body["items"][0]["s"], "elpais")

    def test_items_skips_unknown_source(self):
        self.add("gone", "Old outlet", "https://example.com/b")
        _, body = self.get("/api/items?since=0")
        self.assertEqual(body["items"], [])

    def test_sources_lists_every_source(self):
        _, body = self.get("/api/sources")
        self.assertEqual({s["id"] for s in body["sources"]}, {s["id"] for s in self.sources})

    def test_geo_counts_exile_under_covers(self):
        self.add("meduza", "Новости", "https://example.com/c")
        _, body = self.get("/api/geo?since=0")
        self.assertEqual(body["countries"]["RU"]["items"], 1)
        self.assertNotIn("LV", body["countries"])
        self.assertIn("press_freedom", body)

    def test_post_requires_header(self):
        status, _ = self.post("/api/refresh", header=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.fetcher.refreshed, 0)

    def test_refresh(self):
        status, body = self.post("/api/refresh")
        self.assertEqual((status, body), (200, {"started": True}))

    def test_save_update_export_unsave(self):
        self.add("elpais", "Titular", "https://example.com/a", "Resumen")
        _, body = self.get("/api/items?since=0")
        item_id = body["items"][0]["id"]
        self.assertEqual(self.post("/api/save", {"id": item_id})[0], 200)
        self.assertEqual(self.post("/api/saved/update", {"id": item_id, "note": "para la moción"})[0], 200)
        _, saved = self.get("/api/saved")
        self.assertEqual(saved["saved"][0]["note"], "para la moción")
        self.assertEqual(saved["saved"][0]["country_iso"], "ES")
        status, md = self.get("/api/export.md")
        self.assertEqual(status, 200)
        self.assertIn("## Titular", md)
        self.assertIn("para la moción", md)
        self.post("/api/unsave", {"id": item_id})
        self.assertEqual(self.get("/api/saved")[1]["saved"], [])

    def test_save_unknown_item_is_404(self):
        self.assertEqual(self.post("/api/save", {"id": "nope"})[0], 404)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.get("/api/nope")[0], 404)

    def test_static_js_and_traversal(self):
        self.assertEqual(self.get("/js/main.js")[0], 200)
        self.assertEqual(self.get("/js/..%5C..%5Cserver.js")[0], 404)


if __name__ == "__main__":
    unittest.main()
