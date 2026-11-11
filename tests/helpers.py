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
