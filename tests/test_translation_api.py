import time
import unittest

from tests.helpers import ApiCase

AR_TITLE = "\u0627\u0631\u062a\u0641\u0639 \u0645\u0639\u062f\u0644 \u0627\u0644\u062a\u0636\u062e\u0645"
AR_SUMMARY = "\u0627\u0644\u0623\u062e\u0628\u0627\u0631 \u0627\u0644\u0627\u0642\u062a\u0635\u0627\u062f\u064a\u0629"
ES_TITLE = "Sube la inflaci\u00f3n"
ES_SUMMARY = "Noticias econ\u00f3micas"


class TestTranslationApi(ApiCase):
    def setUp(self):
        super().setUp()
        self.add("ahram", AR_TITLE, "https://example.com/ar", AR_SUMMARY)
        self.add("elpais", "Titular", "https://example.com/es", "Resumen")
        self.ar = self.item_id("https://example.com/ar")
        self.es = self.item_id("https://example.com/es")

    def item_id(self, link):
        return self.store.db.execute("SELECT id FROM items WHERE link = ?", (link,)).fetchone()[0]

    def translate(self, title=True, summary=True, now=None):
        rows = []
        if title:
            rows.append({"item_id": self.ar, "field": "title", "lang": "es", "text": ES_TITLE,
                         "engine": "fake", "error": ""})
        if summary:
            rows.append({"item_id": self.ar, "field": "summary", "lang": "es", "text": ES_SUMMARY,
                         "engine": "fake", "error": ""})
        self.store.save_translations(rows, now)

    def items(self, query="since=0"):
        return self.get("/api/items?" + query)[1]

    def test_items_carry_tr_only_when_translated(self):
        body = self.items()
        self.assertEqual(body["tr_seen"], 0)
        self.assertFalse(any("tr" in i for i in body["items"]))
        self.translate()
        by_id = {i["id"]: i for i in self.items()["items"]}
        self.assertEqual(by_id[self.ar]["tr"], {"title": ES_TITLE, "summary": ES_SUMMARY, "engine": "fake"})
        self.assertNotIn("tr", by_id[self.es])
        self.assertEqual(by_id[self.ar]["title"], AR_TITLE)          # the original is never replaced

    def test_summary_only_translation(self):
        self.translate(title=False)
        item = [i for i in self.items()["items"] if i["id"] == self.ar][0]
        self.assertEqual(item["tr"], {"title": "", "summary": ES_SUMMARY, "engine": "fake"})

    def test_translations_that_arrive_later_reach_the_feed(self):
        first = self.items()
        self.assertNotIn("translated", first)
        self.translate(now=time.time())
        later = self.items("since=%f&tr_since=1" % (time.time() + 60))
        self.assertEqual(later["items"], [])
        self.assertEqual(later["translated"][self.ar]["title"], ES_TITLE)
        self.assertGreater(later["tr_seen"], 1)
        again = self.items("since=%f&tr_since=%r" % (time.time() + 60, later["tr_seen"]))
        self.assertEqual(again["translated"], {})

    def test_old_items_are_not_sent_as_translated(self):
        self.store.db.execute("UPDATE items SET first_seen = 1000 WHERE id = ?", (self.ar,))
        self.store.db.commit()
        self.translate(now=time.time())
        self.assertEqual(self.items("since=0&tr_since=1")["translated"], {})

    def test_search_carries_tr(self):
        self.translate()
        _, body = self.get("/api/search?source=ahram")
        self.assertEqual(body["items"][0]["tr"]["title"], ES_TITLE)

    def test_saved_and_export_carry_both(self):
        self.translate()
        self.post("/api/save", {"id": self.ar})
        self.post("/api/save", {"id": self.es})
        saved = {x["id"]: x for x in self.get("/api/saved")[1]["saved"]}
        self.assertEqual(saved[self.ar]["tr"]["summary"], ES_SUMMARY)
        self.assertNotIn("tr", saved[self.es])
        _, md = self.get("/api/export.md")
        self.assertIn("## " + ES_TITLE, md)
        self.assertIn("- Original title (ar): " + AR_TITLE, md)
        self.assertIn("- Summary (machine translation from ar, fake): " + ES_SUMMARY, md)
        self.assertIn("- Original summary (ar): " + AR_SUMMARY, md)
        self.assertIn("## Titular", md)
        self.assertIn("- Summary: Resumen", md)


if __name__ == "__main__":
    unittest.main()
