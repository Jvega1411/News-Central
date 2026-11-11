import os
import sqlite3
import unittest

from newscentral.store import Store, fts5_available
from tests.helpers import ApiCase

RUSSIAN = "Новости экономики"
ARABIC = "الأخبار الاقتصادية"


def has_fts5():
    db = sqlite3.connect(":memory:")
    try:
        return fts5_available(db)
    finally:
        db.close()


class SearchTests:
    """Shared by both engines. Queries use whole words so FTS and LIKE agree."""

    engine = None

    def setUp(self):
        super().setUp()
        self.add("elpais", "Banco de España sube tipos", "https://example.com/1", "La inflación repunta")
        self.add("bbc", "Bank of England holds rates", "https://example.com/2", "Inflation eases")
        self.add("ahram", ARABIC, "https://example.com/3")
        self.add("meduza", RUSSIAN, "https://example.com/4")
        self.add("ecb", "ECB rates decision", "https://example.com/5", "Monetary policy statement")
        self.add("gone", "Rates at a closed outlet", "https://example.com/6", lang="en")

    def titles(self, query):
        status, body = self.get("/api/search?" + query)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["engine"], self.engine)
        return sorted(i["title"] for i in body["items"])

    def test_single_term(self):
        self.assertEqual(self.titles("q=rates"), ["Bank of England holds rates", "ECB rates decision",
                                                  "Rates at a closed outlet"])

    def test_terms_are_anded_and_case_insensitive(self):
        self.assertEqual(self.titles("q=RATES+england"), ["Bank of England holds rates"])

    def test_matches_summary(self):
        self.assertEqual(self.titles("q=monetary"), ["ECB rates decision"])

    def test_non_latin_text(self):
        self.assertEqual(self.titles("q=" + "%D0%9D%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D0%B8"), [RUSSIAN])

    def test_query_syntax_characters_are_harmless(self):
        for q in ('%22rates', 'rates)', 'rates+OR', 'NEAR(rates', 'rates*', '-rates', 'title:rates', 'rates+%25_'):
            status, body = self.get("/api/search?q=" + q)
            self.assertEqual(status, 200, (q, body))

    def test_filters(self):
        self.assertEqual(self.titles("q=rates&source=bbc"), ["Bank of England holds rates"])
        self.assertEqual(self.titles("q=rates&kind=official"), ["ECB rates decision"])
        self.assertEqual(self.titles("q=rates&scope=Intergovernmental"), ["ECB rates decision"])
        self.assertEqual(self.titles("q=rates&region=Global"), ["Bank of England holds rates"])
        self.assertEqual(self.titles("source=ahram&lang=ar"), [ARABIC])
        self.assertEqual(self.titles("source=ahram&lang=en"), [])

    def test_exile_outlets_match_the_country_they_cover(self):
        self.assertEqual(self.titles("from=0&country=RU"), [RUSSIAN])
        self.assertEqual(self.titles("from=0&country=LV"), [])

    def test_date_range(self):
        self.store.db.execute("UPDATE items SET first_seen = 1000 WHERE source_id = 'bbc'")
        self.store.db.execute("UPDATE items SET first_seen = 2000 WHERE source_id = 'ecb'")
        self.store.db.commit()
        self.assertEqual(self.titles("from=500&to=1500"), ["Bank of England holds rates"])
        self.assertEqual(self.titles("from=500&to=2500"), ["Bank of England holds rates", "ECB rates decision"])
        self.assertEqual(self.titles("q=rates&from=1500&to=2500"), ["ECB rates decision"])

    def test_paging_and_total(self):
        _, body = self.get("/api/search?from=0&limit=2&offset=0")
        self.assertEqual((len(body["items"]), body["total"], body["limit"], body["offset"]), (2, 6, 2, 0))
        _, more = self.get("/api/search?from=0&limit=2&offset=4")
        self.assertEqual(len(more["items"]), 2)
        self.assertFalse({i["id"] for i in body["items"]} & {i["id"] for i in more["items"]})

    def test_limit_is_clamped(self):
        self.assertEqual(self.get("/api/search?from=0&limit=999")[1]["limit"], 200)
        self.assertEqual(self.get("/api/search?from=0&limit=0")[1]["limit"], 1)

    def test_requires_a_criterion(self):
        self.assertEqual(self.get("/api/search")[0], 400)
        self.assertEqual(self.get("/api/search?region=Spain")[0], 400)
        self.assertEqual(self.get("/api/search?q=%22%29")[0], 400)      # no searchable term
        self.assertEqual(self.get("/api/search?q=%25_")[0], 400)
        self.assertEqual(self.get("/api/search?from=abc")[0], 400)

    def test_item_fields_and_removed_source(self):
        _, body = self.get("/api/search?q=inflaci%C3%B3n")
        item = body["items"][0]
        self.assertEqual((item["s"], item["source_name"], item["lang"], item["country_iso"], item["kind"]),
                         ("elpais", "El País", "es", "ES", "news"))
        self.assertIn("t", item)
        _, body = self.get("/api/search?source=gone")
        self.assertEqual((body["items"][0]["source_name"], body["items"][0]["kind"]), ("gone", "news"))


@unittest.skipUnless(has_fts5(), "this SQLite build has no FTS5")
class TestSearchFts(SearchTests, ApiCase):
    engine = "fts"

    def test_diacritics_are_folded(self):
        self.assertEqual(self.titles("q=espana"), ["Banco de España sube tipos"])
        self.assertEqual(self.titles("q=inflacion"), ["Banco de España sube tipos"])

    def test_index_is_rebuilt_when_triggers_were_missing(self):
        path = os.path.join(self.dir, "news.db")
        self.store.db.execute("DROP TRIGGER items_fts_ai")
        self.store.db.commit()
        self.add("bbc", "Untracked zebra story", "https://example.com/7")
        self.assertEqual(self.titles("q=zebra"), [])
        self.store.db.close()
        self.store = Store(path)
        self.app.store = self.store
        self.assertEqual(self.titles("q=zebra"), ["Untracked zebra story"])


class TestSearchLike(SearchTests, ApiCase):
    engine = "like"
    fts = False

    def test_no_fts_triggers_are_left_behind(self):
        names = [r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
        self.assertEqual(names, [])


if __name__ == "__main__":
    unittest.main()
