import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import DEFAULTS, INDEX_FILE, LIVE_ROW_LIMIT, LIVE_WINDOW_HOURS, STATIC_DIR
from .fetcher import source_payload
from .sources import load_countries, load_press_freedom
from .stats import gaps, volume
from .store import search_terms, sort_time


class BadRequest(Exception):
    pass


class App:
    """Everything a request handler needs. One per server; tests build their own."""

    def __init__(self, store, fetcher, config=None):
        self.store = store
        self.fetcher = fetcher
        self.config = dict(DEFAULTS, **(config or {}))
        self.live_row_limit = LIVE_ROW_LIMIT


def status_payload(app):
    f = app.fetcher
    return {"running": f.running, "updated": f.last_cycle, "sources_error": f.sources_error,
            "read_langs": app.config["read_langs"]}


def stats_payload(app, days, now=None):
    now = time.time() if now is None else now
    interval = app.config["refresh_minutes"] * 60
    wide = gaps(app.store.run_starts(now - days * 86400), interval, now - days * 86400, now)
    week = gaps(app.store.run_starts(now - 7 * 86400), interval, now - 7 * 86400, now)
    return {"uptime_7d": week["uptime"], "uptime_30d": wide["uptime"], "days": days,
            "gaps": wide["gaps"], "longest_gap": wide["longest"],
            "volume": volume(app.store.volume_rows(now - days * 86400)),
            "read_langs": app.config["read_langs"]}


def items_payload(app, since):
    """Returns (items, truncated)."""
    min_time = time.time() - LIVE_WINDOW_HOURS * 3600
    known = app.fetcher.source_map()
    rows, truncated = app.store.items(max(since, min_time - 3600), app.live_row_limit)
    out = []
    for r in rows:
        if r["source_id"] not in known:
            continue
        t = sort_time(r["published"], r["first_seen"])
        if t < min_time:
            continue
        out.append({"id": r["id"], "s": r["source_id"], "t": t, "fs": r["first_seen"],
                    "title": r["title"], "link": r["link"], "summary": r["summary"] or "",
                    "lang": r["lang"] or known[r["source_id"]].get("lang", "")})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out, truncated


def search_payload(app, q):
    def one(name):
        return (q.get(name) or [""])[0].strip()

    def number(name, cast):
        value = one(name)
        if not value:
            return None
        try:
            return cast(value)
        except ValueError:
            raise BadRequest("%s must be a number" % name)

    text, source = one("q"), one("source")
    since, until = number("from", float), number("to", float)
    if not search_terms(text) and since is None and not source:
        raise BadRequest("give at least one of q, from or source")
    limit = number("limit", int)
    limit = 50 if limit is None else min(max(limit, 1), 200)
    offset = max(number("offset", int) or 0, 0)
    rows, total, engine = app.store.search(
        text, since, until, one("region"), one("country").upper(), one("scope"), one("lang").lower(),
        one("kind"), source, limit, offset)
    items = [{"id": r["id"], "s": r["source_id"], "t": sort_time(r["published"], r["first_seen"]),
              "fs": r["first_seen"], "title": r["title"], "link": r["link"], "summary": r["summary"] or "",
              "lang": r["lang"] or "", "source_name": r["source_name"] or r["source_id"],
              "region": r["region"] or "", "country_iso": r["country_iso"], "scope": r["scope"],
              "exile": bool(r["exile"]), "covers": r["covers"], "kind": r["kind"] or "news"} for r in rows]
    return {"items": items, "total": total, "engine": engine, "limit": limit, "offset": offset}


def geo_payload(app, since):
    cutoff = time.time() - LIVE_WINDOW_HOURS * 3600 - 3600
    rows, _ = app.store.items(max(since, cutoff), app.live_row_limit)
    out = aggregate_geo(app.fetcher.sources, rows, load_countries(), since)
    out["updated"] = app.fetcher.last_cycle
    out["press_freedom"] = load_press_freedom()
    return out


def aggregate_geo(sources, items, countries, since):
    min_time = time.time() - LIVE_WINDOW_HOURS * 3600
    floor = max(since, min_time)
    by_id = {s["id"]: s for s in sources}
    countries_out, buckets_out = {}, {}
    seen_country, seen_bucket = {}, {}  # key -> set of source ids already counted

    def bump(table, seen, key, sid, meta=None):
        if key not in table:
            table[key] = {"items": 0, "sources": 0}
            if meta:
                table[key].update(meta)
            seen[key] = set()
        table[key]["items"] += 1
        if sid not in seen[key]:
            seen[key].add(sid)
            table[key]["sources"] += 1

    for r in items:
        s = by_id.get(r["source_id"])
        if not s:
            continue
        t = sort_time(r.get("published"), r["first_seen"])
        if t < floor:
            continue
        iso = s.get("covers") if s.get("exile") else s.get("country_iso")
        if iso:
            meta = countries.get(iso, {})
            bump(countries_out, seen_country, iso, s["id"],
                 {"name": meta.get("name", iso), "region": meta.get("region", "")})
        elif s.get("scope"):
            bump(buckets_out, seen_bucket, s["scope"], s["id"])
    return {"countries": countries_out, "buckets": buckets_out}


def export_markdown(app, only_undigested):
    rows = app.store.saved()
    if only_undigested:
        rows = [r for r in rows if not r["digested"]]
    lines = ["# Saved news for digestion", "",
             "Exported %s · %d items" % (time.strftime("%Y-%m-%d %H:%M"), len(rows)), ""]
    for r in rows:
        lines.append("## " + (r["title"] or ""))
        where = ", ".join(x for x in (r["country"], r["lang"]) if x)
        lines.append("- Source: %s%s · %s" % (r["source_name"], " (%s)" % where if where else "", r["region"]))
        if r["published"]:
            lines.append("- Published: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(r["published"])))
        lines.append("- Link: " + (r["link"] or ""))
        if r["summary"]:
            lines.append("- Summary: " + r["summary"])
        if r["note"]:
            lines.append("- My note: " + " ".join(r["note"].split()))
        lines.append("")
    return "\n".join(lines)


def static_path(path):
    """Map a request path to a file under static/, or None if it tries to leave it."""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return os.path.join(STATIC_DIR, *parts)


class Handler(BaseHTTPRequestHandler):
    server_version = "NewsCentral/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _serve_static(self, path, ctype):
        full = static_path(path)
        if not full or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        path = url.path
        try:
            app = self.server.app
            if path in ("/", "/index.html"):
                with open(INDEX_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path.startswith("/js/") and path.endswith(".js"):
                self._serve_static(path, "text/javascript; charset=utf-8")
            elif path == "/world.svg":
                self._serve_static("/world.svg", "image/svg+xml")
            elif path == "/api/status":
                self._json(status_payload(app))
            elif path == "/api/items":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    since = 0.0
                payload = status_payload(app)
                payload["items"], payload["truncated"] = items_payload(app, since)
                self._json(payload)
            elif path == "/api/geo":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    since = 0.0
                self._json(geo_payload(app, since))
            elif path == "/api/sources":
                payload = status_payload(app)
                payload["sources"] = source_payload(app.fetcher.sources, app.store)
                self._json(payload)
            elif path == "/api/saved":
                self._json({"saved": app.store.saved()})
            elif path == "/api/export.md":
                only = (q.get("only") or [""])[0] == "undigested"
                extra = {}
                if (q.get("download") or [""])[0]:
                    extra["Content-Disposition"] = (
                        'attachment; filename="news-central-saved-%s.md"' % time.strftime("%Y-%m-%d"))
                self._send(200, export_markdown(app, only).encode("utf-8"), "text/markdown; charset=utf-8", extra)
            elif path == "/api/stats":
                try:
                    days = int((q.get("days") or ["30"])[0])
                except ValueError:
                    days = 0
                if not 1 <= days <= 3650:
                    return self._json({"error": "days must be between 1 and 3650"}, 400)
                self._json(stats_payload(app, days))
            elif path == "/api/search":
                self._json(search_payload(app, q))
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BadRequest as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        # The custom header blocks other websites from posting to this local server.
        if self.headers.get("X-NewsCentral") != "1":
            return self._json({"error": "forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return self._json({"error": "bad request"}, 400)
        path = urllib.parse.urlsplit(self.path).path
        item_id = str(body.get("id", ""))
        try:
            app = self.server.app
            if path == "/api/refresh":
                self._json({"started": app.fetcher.refresh_now()})
            elif path == "/api/save":
                item = app.store.get_item(item_id)
                if not item:
                    return self._json({"error": "item not found"}, 404)
                app.store.save(item, app.fetcher.source_map().get(item["source_id"], {}))
                self._json({"ok": True})
            elif path == "/api/unsave":
                app.store.unsave(item_id)
                self._json({"ok": True})
            elif path == "/api/saved/update":
                app.store.update_saved(item_id, body.get("note"), body.get("digested"))
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json({"error": str(e)}, 500)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second copy bind the same port, so the
    # "already running" check would never fire. Elsewhere it only skips TIME_WAIT.
    allow_reuse_address = os.name != "nt"
