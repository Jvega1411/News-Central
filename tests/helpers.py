import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from newscentral.api import App, Handler, Server
from newscentral.store import Store

SOURCES = [
    {"id": "elpais", "name": "El País", "region": "Spain", "country_iso": "ES", "lang": "es", "kind": "news"},
    {"id": "bbc", "name": "BBC World", "region": "Global", "country_iso": "GB", "lang": "en", "kind": "news"},
    {"id": "ahram", "name": "Al-Ahram", "region": "Middle East & N. Africa", "country_iso": "EG",
     "lang": "ar", "kind": "news", "state_media": True},
    {"id": "meduza", "name": "Meduza", "region": "Eurasia", "country_iso": "LV", "exile": True,
     "covers": "RU", "lang": "ru", "kind": "news"},
    {"id": "ecb", "name": "ECB", "region": "Europe & EU", "country_iso": None,
     "scope": "Intergovernmental", "lang": "en", "kind": "official"},
]


class FakeFetcher:
    """Stands in for the fetch thread: same attributes the API reads, no network."""

    def __init__(self, sources):
        self.sources = sources
        self.running = False
        self.last_cycle = 0.0
        self.sources_error = ""
        self.refreshed = 0

    def source_map(self):
        return {s["id"]: s for s in self.sources}

    def refresh_now(self):
        self.refreshed += 1
        return True


class ApiCase(unittest.TestCase):
    """Starts the real handler on an ephemeral port against a temporary database."""

    sources = SOURCES
    config = None
    fts = True

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "news.db"), fts=self.fts)
        self.store.sync_sources(self.sources)
        self.fetcher = FakeFetcher(list(self.sources))
        self.app = App(self.store, self.fetcher, self.config)
        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.httpd.app = self.app
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _open(self, req):
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def get(self, path):
        status, ctype, body = self._open(urllib.request.Request(self.base + path))
        if "json" in ctype:
            return status, json.loads(body.decode("utf-8"))
        return status, body.decode("utf-8")

    def post(self, path, body=None, header=True):
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-NewsCentral"] = "1"
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode("utf-8"),
                                     headers=headers, method="POST")
        status, ctype, raw = self._open(req)
        return status, json.loads(raw.decode("utf-8"))

    def add(self, source_id, title, link, summary="", published=None, lang=None):
        if lang is None:
            lang = self.fetcher.source_map().get(source_id, {}).get("lang")
        self.store.add_items(source_id, [{"title": title, "link": link, "summary": summary,
                                          "published": published}], lang)


class TranslationCase(unittest.TestCase):
    """A temporary database with items in Arabic, Russian, Spanish and English, at known times.
    self.ids maps a short key to the item id."""

    READ = ["es", "en"]

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "news.db"))
        self.store.sync_sources(SOURCES)
        self.ids = {}
        self.add_at("old_ar", "ahram", "الأخبار", "ملخص", 1000)
        self.add_at("new_ru", "meduza", "Новости", "Текст", 2000)
        self.add_at("es", "elpais", "Titular", "Resumen", 3000)
        self.add_at("en", "bbc", "Headline", "Summary", 4000)
        self.add_at("ar_no_summary", "ahram", "خبر", "", 500)

    def tearDown(self):
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_at(self, key, source, title, summary, first_seen):
        link = "https://example.com/" + key
        lang = {s["id"]: s["lang"] for s in SOURCES}[source]
        self.store.add_items(source, [{"title": title, "link": link, "summary": summary}], lang)
        item_id = self.store.db.execute("SELECT id FROM items WHERE link = ?", (link,)).fetchone()[0]
        self.store.db.execute("UPDATE items SET first_seen = ? WHERE id = ?", (first_seen, item_id))
        self.store.db.commit()
        self.ids[key] = item_id
        return item_id

    def queue(self, limit=50):
        return [(u["item_id"], u["field"]) for u in self.store.pending_translations(self.READ, "es", limit)]

    def key(self, name, field):
        return (self.ids[name], field)


def tr_ok(item_id, field, text="texto", engine="fake"):
    return {"item_id": item_id, "field": field, "lang": "es", "text": text, "engine": engine, "error": ""}


def tr_failed(item_id, field, error="boom"):
    return {"item_id": item_id, "field": field, "lang": "es", "text": "", "engine": "fake", "error": error}


def load_tool(name):
    """Import tools/<name>.py as a module (tools/ is not a package)."""
    import importlib.util
    from newscentral.config import BASE_DIR
    path = os.path.join(BASE_DIR, "tools", name + ".py")
    spec = importlib.util.spec_from_file_location("tool_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
