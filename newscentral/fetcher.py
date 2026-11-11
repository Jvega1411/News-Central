import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .backup import run_backup
from .config import SOURCES_FILE, REFRESH_SECONDS, ERROR_RETRY_SECONDS, WORKERS
from .feeds import configured_feeds, fetch_source
from .http_client import short_error
from .sources import load_sources, country_label


def source_payload(sources, store):
    states = store.states()
    out = []
    for s in sources:
        st = states.get(s["id"], {})
        out.append({
            "id": s["id"], "name": s.get("name", s["id"]), "region": s.get("region", "Other"),
            "country_iso": s.get("country_iso"), "scope": s.get("scope"),
            "exile": bool(s.get("exile")), "covers": s.get("covers"),
            "country": country_label(s),
            "lang": s.get("lang", ""), "kind": s.get("kind", "news"),
            "state_media": bool(s.get("state_media")), "paywall": bool(s.get("paywall")),
            "status": st.get("status") or "pending", "error": st.get("error") or "",
            "count": st.get("count") or 0, "last_ok": st.get("last_ok"), "last_try": st.get("last_try"),
            "feed": st.get("feed_used") or (configured_feeds(s)[:1] or [""])[0], "site": s.get("site", ""),
        })
    return out


class Fetcher(threading.Thread):
    def __init__(self, store, refresh_seconds=REFRESH_SECONDS, status_file=None, backup_dir=None):
        super().__init__(daemon=True)
        self.store = store
        self.refresh_seconds = refresh_seconds
        self.status_file = status_file
        self.backup_dir = backup_dir
        self.last_backup = 0.0
        self.wake = threading.Event()
        self.force_next = True   # first cycle after start retries everything, failing sources included
        self.running = False
        self.last_cycle = 0.0
        self.sources = []
        self.sources_error = ""
        self._synced_mtime = None
        self.reload_sources()

    def reload_sources(self):
        try:
            self.sources = load_sources()
            self.sources_error = ""
            mtime = self.sources_mtime()
            if mtime != self._synced_mtime:      # first load, or sources.json edited
                self.store.sync_sources(self.sources)
                self._synced_mtime = mtime
        except Exception as e:
            self.sources_error = "Could not read sources.json: %s" % e
        return self.sources

    def source_map(self):
        return {s["id"]: s for s in self.sources}

    def refresh_now(self):
        if self.running:
            return False
        self.force_next = True
        self.wake.set()
        return True

    @staticmethod
    def sources_mtime():
        try:
            return os.path.getmtime(SOURCES_FILE)
        except OSError:
            return 0

    def run(self):
        while True:
            force, self.force_next = self.force_next, False
            try:
                self.cycle(force)
            except Exception as e:
                print("fetch cycle failed:", e, file=sys.stderr, flush=True)
            deadline = time.time() + self.refresh_seconds
            mtime = self.sources_mtime()
            while time.time() < deadline:
                if self.wake.wait(5):
                    self.wake.clear()
                    break
                if self.sources_mtime() != mtime:  # sources.json edited: fetch again right away
                    self.force_next = True
                    break

    def cycle(self, force=False):
        self.running = True
        try:
            started = time.time()
            sources = self.reload_sources()
            states = self.store.states()
            now = time.time()
            todo = [s for s in sources
                    if force or not states.get(s["id"])
                    or states[s["id"]].get("status") != "error"
                    or now - (states[s["id"]].get("last_try") or 0) >= ERROR_RETRY_SECONDS]
            new_total = 0
            errors = 0
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {pool.submit(fetch_source, s, states.get(s["id"])): s for s in todo}
                for fut in as_completed(futures):
                    s = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as e:
                        result = {"feed": (configured_feeds(s)[:1] or [""])[0], "items": [], "status": "error", "error": short_error(e)}
                    if result["status"] == "error":
                        errors += 1
                    try:
                        if result["items"]:
                            new_total += self.store.add_items(s["id"], result["items"], s.get("lang"))
                        self.store.set_state(s["id"], result)
                    except Exception as e:
                        print("store write failed for %s: %s" % (s["id"], e), file=sys.stderr, flush=True)
            self.last_cycle = time.time()
            self.store.add_run(started, self.last_cycle, len(todo), new_total, errors)
            self.write_status()
            self.maybe_backup()
            print(time.strftime("%Y-%m-%d %H:%M:%S"), "checked %d sources, %d new items" % (len(todo), new_total),
                  flush=True)
        finally:
            self.running = False

    def maybe_backup(self):
        """Once per UTC day, after a fetch cycle. Never lets a failure stop the fetch loop."""
        if not self.backup_dir:
            return
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self.last_backup and time.strftime("%Y-%m-%d", time.gmtime(self.last_backup)) == today:
            return
        try:
            run_backup(self.store.path, self.backup_dir)
            self.last_backup = time.time()
        except Exception as e:
            print("backup failed: %s" % e, file=sys.stderr, flush=True)

    def write_status(self):
        if not self.status_file:
            return
        try:
            payload = {"updated": self.last_cycle, "sources": source_payload(self.sources, self.store)}
            tmp = self.status_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.status_file)
        except Exception:
            pass
