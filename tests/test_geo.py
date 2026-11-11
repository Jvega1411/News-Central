import time, unittest
from newscentral import api

COUNTRIES = {"ES": {"name": "Spain", "region": "Spain"},
             "LV": {"name": "Latvia", "region": "Europe & EU"},
             "RU": {"name": "Russia", "region": "Eurasia"}}

def item(sid, age_s=10):
    now = time.time()
    return {"source_id": sid, "published": now - age_s, "first_seen": now - age_s}

class TestGeo(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"id": "elpais", "country_iso": "ES", "region": "Spain"},
            {"id": "meduza", "country_iso": "LV", "exile": True, "covers": "RU", "region": "Eurasia"},
            {"id": "ps", "country_iso": None, "scope": "Global", "region": "Global"},
        ]

    def test_counts_country(self):
        out = api.aggregate_geo(self.sources, [item("elpais"), item("elpais")], COUNTRIES, 0)
        self.assertEqual(out["countries"]["ES"]["items"], 2)
        self.assertEqual(out["countries"]["ES"]["sources"], 1)
        self.assertEqual(out["countries"]["ES"]["name"], "Spain")

    def test_exile_counts_under_covers(self):
        out = api.aggregate_geo(self.sources, [item("meduza")], COUNTRIES, 0)
        self.assertEqual(out["countries"]["RU"]["items"], 1)
        self.assertNotIn("LV", out["countries"])

    def test_scope_bucket(self):
        out = api.aggregate_geo(self.sources, [item("ps")], COUNTRIES, 0)
        self.assertEqual(out["buckets"]["Global"]["items"], 1)

    def test_since_filters_old_items(self):
        out = api.aggregate_geo(self.sources, [item("elpais", age_s=10)], COUNTRIES, time.time() - 1)
        self.assertNotIn("ES", out["countries"])

if __name__ == "__main__":
    unittest.main()
