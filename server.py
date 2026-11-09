#!/usr/bin/env python3
# News Central: local world-news reader.
# Pulls the RSS/Atom feeds listed in sources.json, serves a reader at
# http://127.0.0.1:8787 and keeps saved items in data/news.db.
# Standard library only, Python 3.8+.

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.entities import name2codepoint
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
COUNTRIES_FILE = os.path.join(BASE_DIR, "geo", "countries.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "news.db")
STATUS_FILE = os.path.join(DATA_DIR, "sources_status.json")
INDEX_FILE = os.path.join(BASE_DIR, "static", "index.html")

REFRESH_SECONDS = 900          # background fetch every 15 minutes
ERROR_RETRY_SECONDS = 3600     # failing sources retried hourly, or on manual refresh
KEEP_HOURS = 96                # items older than this are dropped (saved items are kept)
FETCH_TIMEOUT = 20
WORKERS = 16
MAX_ITEMS_PER_FEED = 60
MAX_BYTES = 12 * 1024 * 1024
SUMMARY_CHARS = 500
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15 NewsCentral/1.0")
ALT_USER_AGENT = "NewsCentral/1.0 (personal RSS reader)"
FEED_ACCEPT = ("application/rss+xml, application/atom+xml, application/rdf+xml, "
               "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5")
BLOCK_CODES = (401, 403, 406, 429, 503)
CURL = shutil.which("curl")


# ---------------------------------------------------------------- network

def make_ssl_context():
    ctx = ssl.create_default_context()
    # python.org builds on macOS ship without CA certificates; the system bundle fixes that.
    for path in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl@3/cert.pem",
                 "/usr/local/etc/openssl@1.1/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.exists(path):
            try:
                ctx.load_verify_locations(path)
            except Exception:
                pass
    try:
        import certifi  # optional
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


SSL_CTX = make_ssl_context()


class _Redirects(urllib.request.HTTPRedirectHandler):
    # Python < 3.11 does not follow 308 redirects, which many news CDNs use.
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, 307, msg, headers)


OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=SSL_CTX), _Redirects())


class NotModified(Exception):
    pass


def http_get(url, etag=None, lastmod=None, accept=None, ua=None):
    headers = {
        "User-Agent": ua or USER_AGENT,
        "Accept": accept or FEED_ACCEPT,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en, es;q=0.9, *;q=0.5",
    }
    if etag:
        headers["If-None-Match"] = etag
    if lastmod:
        headers["If-Modified-Since"] = lastmod
    req = urllib.request.Request(url, headers=headers)
    try:
        with OPENER.open(req, timeout=FETCH_TIMEOUT) as resp:
            data = resp.read(MAX_BYTES)
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            final_url = resp.geturl()
            new_etag = resp.headers.get("ETag")
            new_lastmod = resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified()
        raise
    if encoding == "gzip" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif encoding == "deflate":
        try:
            data = zlib.decompress(data)
        except zlib.error:
            data = zlib.decompress(data, -zlib.MAX_WBITS)
    return data, final_url, new_etag, new_lastmod


def curl_get(url, ua, accept=None):
    """Second opinion for sites that refuse Python's HTTP client."""
    if not CURL:
        raise RuntimeError("curl not available")
    cmd = [CURL, "-sS", "-L", "--compressed", "-m", str(FETCH_TIMEOUT), "-A", ua,
           "-H", "Accept: " + (accept or FEED_ACCEPT), "-H", "Accept-Language: en, es;q=0.9",
           "-w", "\n%{http_code} %{url_effective}", url]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=FETCH_TIMEOUT + 5)
    if proc.returncode != 0:
        raise RuntimeError("curl: " + proc.stderr.decode("utf-8", "ignore").strip()[:100])
    body, _, tail = proc.stdout.rpartition(b"\n")
    code, _, final_url = tail.decode("utf-8", "ignore").partition(" ")
    if code != "200":
        raise urllib.error.HTTPError(url, int(code or 0), "curl", None, None)
    return body, final_url or url, None, None


def get_with_fallback(url, accept=None, etag=None, lastmod=None, check=None):
    """GET url; if the site blocks us (403 etc. or a bot-check page), retry with other clients.
    check(data, final_url) parses the body and raises ValueError("...web page...") on a bot page."""
    try:
        data, final_url, et, lm = http_get(url, etag, lastmod, accept)
        return (check(data, final_url) if check else data), final_url, et, lm
    except NotModified:
        raise
    except urllib.error.HTTPError as e:
        if e.code not in BLOCK_CODES:
            raise
        first = e
    except ValueError as e:
        if "web page" not in str(e):
            raise
        first = e
    attempts = (lambda: http_get(url, accept=accept, ua=ALT_USER_AGENT),
                lambda: curl_get(url, USER_AGENT, accept),
                lambda: curl_get(url, ALT_USER_AGENT, accept))
    for attempt in attempts:
        try:
            data, final_url, et, lm = attempt()
            return (check(data, final_url) if check else data), final_url, et, lm
        except Exception:
            continue
    raise first


def short_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %s" % exc.code
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return "SSL error: %s" % (getattr(reason, "reason", None) or reason)
        if isinstance(reason, socket.timeout):
            return "timeout"
        return str(reason)[:120]
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    return ("%s: %s" % (type(exc).__name__, exc))[:160]


# ---------------------------------------------------------------- parsing

XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
APPEARED_RE = re.compile(r"\s*The post .{1,300}? appeared first on .{1,200}?\.?\s*$", re.S)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?", re.I)
LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "ocid", "cmpid", "mc_cid", "mc_eid")


def decode_bytes(data):
    m = re.search(rb'encoding=["\']([A-Za-z0-9_.\-]+)["\']', data[:300])
    encodings = [m.group(1).decode("ascii", "ignore")] if m else []
    encodings += ["utf-8", "cp1252", "latin-1"]
    for enc in encodings:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def repair_xml(data):
    text = decode_bytes(data).lstrip("﻿ \t\r\n")
    text = re.sub(r"^<\?xml[^>]*\?>", "", text)
    text = CTRL_RE.sub("", text)

    def fix_entity(m):
        name = m.group(1)
        if name in XML_ENTITIES:
            return m.group(0)
        cp = name2codepoint.get(name)
        return "&#%d;" % cp if cp else ""

    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", fix_entity, text)
    text = re.sub(r"&(?!(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#x[0-9A-Fa-f]+);)", "&amp;", text)
    return text.encode("utf-8")


def clean_text(value, limit=None):
    if not value:
        return ""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(html.unescape(s))
    s = TAG_RE.sub(" ", s)
    s = APPEARED_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        cut = s[:limit]
        if " " in cut[limit // 2:]:
            cut = cut.rsplit(" ", 1)[0]
        s = cut.rstrip(" ,;:.") + "…"
    return s


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    m = ISO_RE.search(value)
    if not m:
        return None
    y, mo, d, hh, mi, ss, tz = m.groups()
    try:
        ts = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0),
                      tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    if tz and tz.upper() != "Z":
        digits = tz[1:].replace(":", "")
        offset = int(digits[:2]) * 3600 + int(digits[2:4] or 0) * 60
        ts = ts - offset if tz[0] == "+" else ts + offset
    return ts


def strip_namespaces(root):
    for el in root.iter():
        tag = el.tag
        if isinstance(tag, str) and tag[:1] == "{":
            el.tag = tag.split("}", 1)[1]
        if el.attrib:
            for key in list(el.attrib):
                if key[:1] == "{":
                    el.attrib[key.split("}", 1)[1]] = el.attrib.pop(key)


def child_text(el, *names):
    for name in names:
        for child in el.findall(name):
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def entry_link(el, base):
    orig = child_text(el, "origLink")  # FeedBurner keeps the real URL here
    if orig:
        return orig
    fallback = ""
    for link in el.findall("link"):
        href = (link.get("href") or "").strip()
        if href:
            rel = (link.get("rel") or "alternate").lower()
            if rel == "alternate":
                return urllib.parse.urljoin(base, href)
            if rel not in ("replies", "enclosure", "self", "edit") and not fallback:
                fallback = urllib.parse.urljoin(base, href)
        else:
            text = (link.text or "").strip()
            if text:
                return urllib.parse.urljoin(base, text)
    if fallback:
        return fallback
    guid = el.find("guid")
    if guid is not None and (guid.text or "").strip().startswith("http"):
        return guid.text.strip()
    about = el.get("about") or ""
    return about if about.startswith("http") else ""


def normalize_link(url):
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith(TRACKING_PARAMS)]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path,
                                    urllib.parse.urlencode(query, doseq=True), ""))


def parse_entry(el, base):
    title = clean_text(child_text(el, "title"), 300)
    link = entry_link(el, base)
    if not title or not link.startswith(("http://", "https://")):
        return None
    summary = clean_text(child_text(el, "description", "summary", "encoded", "content"), SUMMARY_CHARS)
    if summary == title:
        summary = ""
    published = parse_date(child_text(el, "pubDate", "published", "updated", "date",
                                      "issued", "modified", "created"))
    return {"title": title, "link": normalize_link(link), "summary": summary, "published": published}


def regex_items(text, base):
    """Last resort for feeds too broken for the XML parser."""
    out = []
    for m in re.finditer(r"(?is)<(item|entry)\b[^>]*>(.*?)</\1>", text):
        body = m.group(2)

        def grab(pattern):
            mm = re.search(pattern, body, re.I | re.S)
            if not mm:
                return ""
            value = mm.group(mm.lastindex)
            return re.sub(r"(?s)^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", value).strip()

        title = clean_text(grab(r"<title[^>]*>(.*?)</title>"), 300)
        link = html.unescape(grab(r"<link[^>]*href=[\"']([^\"']+)[\"']")
                             or grab(r"<link[^>]*>(.*?)</link>")
                             or grab(r"<guid[^>]*>(https?://.*?)</guid>"))
        link = urllib.parse.urljoin(base, link) if link else ""
        if not title or not link.startswith(("http://", "https://")):
            continue
        out.append({
            "title": title,
            "link": normalize_link(link),
            "summary": clean_text(grab(r"<(description|summary)[^>]*>(.*?)</\1>"), SUMMARY_CHARS),
            "published": parse_date(grab(r"<(pubDate|published|updated|dc:date)[^>]*>(.*?)</\1>")),
        })
    return out


def parse_feed(data, base):
    head = data[:1024].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise ValueError("got a web page, not a feed")
    root = None
    for attempt in (lambda: data, lambda: repair_xml(data)):
        try:
            root = ET.fromstring(attempt())
            break
        except ET.ParseError:
            continue
    if root is None:
        items = regex_items(decode_bytes(data), base)
        if not items:
            raise ValueError("unreadable feed")
        return items
    strip_namespaces(root)
    out = []
    for el in list(root.iter("item")) + list(root.iter("entry")):
        item = parse_entry(el, base)
        if item:
            out.append(item)
    return out


def discover_feeds(site):
    data, final_url, _, _ = get_with_fallback(site, accept="text/html,application/xhtml+xml,*/*;q=0.8")
    text = decode_bytes(data[:800000])
    found = []
    for tag in LINK_TAG_RE.findall(text):
        low = tag.lower()
        if "alternate" in low and ("rss+xml" in low or "atom+xml" in low or "rdf+xml" in low):
            m = HREF_RE.search(tag)
            if m:
                url = urllib.parse.urljoin(final_url, html.unescape(m.group(1).strip()))
                if url not in found and "comment" not in url.lower():
                    found.append(url)
    for guess in ("/feed/", "/rss", "/rss.xml", "/feed", "/index.xml", "/atom.xml"):
        url = urllib.parse.urljoin(final_url, guess)
        if url not in found:
            found.append(url)
    return found[:10]


def configured_feeds(src):
    """`feed` in sources.json may be one URL or a list of candidate URLs."""
    feeds = src.get("feed") or []
    if isinstance(feeds, str):
        feeds = [feeds]
    return [u.strip() for u in feeds if isinstance(u, str) and u.strip()]


def fetch_source(src, state):
    """Try the last working feed, then the configured ones, then auto-discovery on the site."""
    state = state or {}
    configured = configured_feeds(src)
    used = (state.get("feed_used") or "").strip()
    candidates = []
    for url in [used] + configured:
        if url and url not in candidates:
            candidates.append(url)
    errors = []
    for url in candidates:
        same = url == used
        try:
            items, _, etag, lastmod = get_with_fallback(
                url, etag=state.get("etag") if same else None,
                lastmod=state.get("lastmod") if same else None, check=parse_feed)
            if items:
                return {"feed": url, "items": items[:MAX_ITEMS_PER_FEED],
                        "status": "ok" if url in configured else "discovered",
                        "error": "", "etag": etag, "lastmod": lastmod}
            errors.append("feed is empty")
        except NotModified:
            status = state.get("status")
            if status not in ("ok", "discovered"):
                status = "ok" if url in configured else "discovered"
            return {"feed": url, "items": [], "status": status, "error": "",
                    "etag": state.get("etag"), "lastmod": state.get("lastmod"), "not_modified": True}
        except Exception as e:
            errors.append(short_error(e))
    site = (src.get("site") or "").strip()
    if site:
        try:
            for url in discover_feeds(site):
                if url in candidates:
                    continue
                try:
                    items, _, etag, lastmod = get_with_fallback(url, check=parse_feed)
                except Exception:
                    continue
                if items:
                    return {"feed": url, "items": items[:MAX_ITEMS_PER_FEED], "status": "discovered",
                            "error": "", "etag": etag, "lastmod": lastmod}
            errors.append("no feed found on the site")
        except Exception as e:
            errors.append("site: " + short_error(e))
    # keep only distinct messages so the Sources table stays readable
    seen, msgs = set(), []
    for m in errors:
        if m not in seen:
            seen.add(m)
            msgs.append(m)
    return {"feed": used or (configured[0] if configured else ""), "items": [], "status": "error",
            "error": (" · ".join(msgs) or "no feed configured")[:300], "etag": None, "lastmod": None}


def sort_time(published, first_seen):
    # Trust the feed's date unless it is in the future (bad time zones).
    if published and published <= first_seen + 3600:
        return published
    return first_seen


_COUNTRIES = None

def load_countries():
    global _COUNTRIES
    if _COUNTRIES is None:
        with open(COUNTRIES_FILE, encoding="utf-8") as f:
            _COUNTRIES = json.load(f)
    return _COUNTRIES


def load_sources():
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("sources", []) if isinstance(data, dict) else data
    out, seen = [], set()
    for s in raw:
        sid = str(s.get("id") or "").strip()
        if not sid or sid in seen or s.get("disabled"):
            continue
        seen.add(sid)
        s["id"] = sid
        out.append(s)
    return out


SCOPE_VOCAB = ("Global", "Intergovernmental", "Transnational-regional")


def validate_sources(sources, countries):
    errors = []
    for s in sources:
        sid = s.get("id", "?")
        if "country" in s:
            errors.append("%s: legacy free-text 'country' field present" % sid)
        iso = s.get("country_iso")
        scope = s.get("scope")
        if iso:
            if iso not in countries:
                errors.append("%s: country_iso %r not in country reference" % (sid, iso))
        elif scope:
            if scope not in SCOPE_VOCAB:
                errors.append("%s: scope %r not in %r" % (sid, scope, SCOPE_VOCAB))
        else:
            errors.append("%s: needs either country_iso or scope" % sid)
        if s.get("exile"):
            cov = s.get("covers")
            if not cov:
                errors.append("%s: exile source missing 'covers'" % sid)
            elif cov not in countries:
                errors.append("%s: covers %r not in country reference" % (sid, cov))
    return errors


# ---------------------------------------------------------------- migrations

MIGRATIONS = []  # list of (version, fn(db)); applied in ascending order


def migration(version):
    def register(fn):
        MIGRATIONS.append((version, fn))
        return fn
    return register


def run_migrations(db, lock):
    with lock:
        current = db.execute("PRAGMA user_version").fetchone()[0]
        for version, fn in sorted(MIGRATIONS, key=lambda t: t[0]):
            if version > current:
                fn(db)
                db.execute("PRAGMA user_version = %d" % version)  # int only, never user input
                db.commit()


@migration(1)
def _m1_saved_country_iso(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(saved)")]
    if "country_iso" not in cols:
        db.execute("ALTER TABLE saved ADD COLUMN country_iso TEXT DEFAULT ''")


SCHEMA_VERSION = max((v for v, _ in MIGRATIONS), default=0)


# ---------------------------------------------------------------- storage

class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT, link TEXT,
                    summary TEXT, published REAL, first_seen REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS items_first_seen ON items(first_seen);
                CREATE TABLE IF NOT EXISTS saved (
                    id TEXT PRIMARY KEY, source_id TEXT, source_name TEXT, region TEXT,
                    country TEXT, lang TEXT, title TEXT, link TEXT, summary TEXT,
                    published REAL, saved_at REAL, note TEXT DEFAULT '', digested INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS feed_state (
                    source_id TEXT PRIMARY KEY, feed_used TEXT, status TEXT, error TEXT,
                    last_try REAL, last_ok REAL, count INTEGER DEFAULT 0, etag TEXT, lastmod TEXT);
            """)
            self.db.commit()
        run_migrations(self.db, self.lock)

    def add_items(self, source_id, items):
        now = time.time()
        rows = [(hashlib.sha1(it["link"].encode("utf-8")).hexdigest()[:16], source_id, it["title"],
                 it["link"], it.get("summary", ""), it.get("published"), now) for it in items]
        with self.lock:
            before = self.db.total_changes
            self.db.executemany("INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?)", rows)
            self.db.commit()
            return self.db.total_changes - before

    def set_state(self, source_id, result):
        now = time.time()
        with self.lock:
            prev = self.db.execute("SELECT * FROM feed_state WHERE source_id=?", (source_id,)).fetchone()
            ok = result["status"] in ("ok", "discovered")
            last_ok = now if ok else (prev["last_ok"] if prev else None)
            if result.get("not_modified"):
                count = prev["count"] if prev else 0
            else:
                count = len(result["items"])
            feed_used = result.get("feed") or (prev["feed_used"] if prev else None)
            self.db.execute("INSERT OR REPLACE INTO feed_state VALUES (?,?,?,?,?,?,?,?,?)",
                            (source_id, feed_used, result["status"], result.get("error", ""), now,
                             last_ok, count, result.get("etag"), result.get("lastmod")))
            self.db.commit()

    def states(self):
        with self.lock:
            return {r["source_id"]: dict(r) for r in self.db.execute("SELECT * FROM feed_state")}

    def items(self, min_first_seen):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM items WHERE first_seen >= ? ORDER BY first_seen DESC LIMIT 30000",
                (min_first_seen,))]

    def prune(self, cutoff):
        with self.lock:
            self.db.execute("DELETE FROM items WHERE first_seen < ? AND COALESCE(published, first_seen) < ?",
                            (cutoff, cutoff))
            self.db.commit()

    def get_item(self, item_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return dict(r) if r else None

    def save(self, item, src):
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO saved (id, source_id, source_name, region, country, lang, title, "
                "link, summary, published, saved_at, country_iso) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], item["source_id"], src.get("name", item["source_id"]), src.get("region", ""),
                 _country_label(src), src.get("lang", ""), item["title"], item["link"],
                 item["summary"], sort_time(item["published"], item["first_seen"]), time.time(),
                 src.get("country_iso") or ""))
            self.db.commit()

    def unsave(self, item_id):
        with self.lock:
            self.db.execute("DELETE FROM saved WHERE id=?", (item_id,))
            self.db.commit()

    def update_saved(self, item_id, note=None, digested=None):
        with self.lock:
            if note is not None:
                self.db.execute("UPDATE saved SET note=? WHERE id=?", (str(note)[:5000], item_id))
            if digested is not None:
                self.db.execute("UPDATE saved SET digested=? WHERE id=?", (1 if digested else 0, item_id))
            self.db.commit()

    def saved(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM saved ORDER BY saved_at DESC")]


# ---------------------------------------------------------------- fetch loop


def _country_label(s):
    countries = load_countries()
    iso = s.get("country_iso")
    if iso:
        name = countries.get(iso, {}).get("name", iso)
        if s.get("exile") and s.get("covers"):
            cov = countries.get(s["covers"], {}).get("name", s["covers"])
            return "%s (exile, covers %s)" % (name, cov)
        return name
    return s.get("scope") or ""


def source_payload(sources, store):
    states = store.states()
    out = []
    for s in sources:
        st = states.get(s["id"], {})
        out.append({
            "id": s["id"], "name": s.get("name", s["id"]), "region": s.get("region", "Other"),
            "country_iso": s.get("country_iso"), "scope": s.get("scope"),
            "exile": bool(s.get("exile")), "covers": s.get("covers"),
            "country": _country_label(s),
            "lang": s.get("lang", ""), "kind": s.get("kind", "news"),
            "state_media": bool(s.get("state_media")), "paywall": bool(s.get("paywall")),
            "status": st.get("status") or "pending", "error": st.get("error") or "",
            "count": st.get("count") or 0, "last_ok": st.get("last_ok"), "last_try": st.get("last_try"),
            "feed": st.get("feed_used") or (configured_feeds(s)[:1] or [""])[0], "site": s.get("site", ""),
        })
    return out


class Fetcher(threading.Thread):
    def __init__(self, store):
        super().__init__(daemon=True)
        self.store = store
        self.wake = threading.Event()
        self.force_next = True   # first cycle after start retries everything, failing sources included
        self.running = False
        self.last_cycle = 0.0
        self.sources = []
        self.sources_error = ""
        self.reload_sources()

    def reload_sources(self):
        try:
            self.sources = load_sources()
            self.sources_error = ""
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
            deadline = time.time() + REFRESH_SECONDS
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
            sources = self.reload_sources()
            states = self.store.states()
            now = time.time()
            todo = [s for s in sources
                    if force or not states.get(s["id"])
                    or states[s["id"]].get("status") != "error"
                    or now - (states[s["id"]].get("last_try") or 0) >= ERROR_RETRY_SECONDS]
            new_total = 0
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {pool.submit(fetch_source, s, states.get(s["id"])): s for s in todo}
                for fut in as_completed(futures):
                    s = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as e:
                        result = {"feed": (configured_feeds(s)[:1] or [""])[0], "items": [], "status": "error", "error": short_error(e)}
                    if result["items"]:
                        new_total += self.store.add_items(s["id"], result["items"])
                    self.store.set_state(s["id"], result)
            self.store.prune(time.time() - KEEP_HOURS * 3600)
            self.last_cycle = time.time()
            self.write_status()
            print(time.strftime("%H:%M:%S"), "checked %d sources, %d new items" % (len(todo), new_total),
                  flush=True)
        finally:
            self.running = False

    def write_status(self):
        try:
            payload = {"updated": self.last_cycle, "sources": source_payload(self.sources, self.store)}
            tmp = STATUS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATUS_FILE)
        except Exception:
            pass


# ---------------------------------------------------------------- web server

STORE = None
FETCHER = None


def status_payload():
    return {"running": FETCHER.running, "updated": FETCHER.last_cycle, "sources_error": FETCHER.sources_error}


def items_payload(since):
    min_time = time.time() - KEEP_HOURS * 3600
    known = FETCHER.source_map()
    out = []
    for r in STORE.items(max(since, min_time - 3600)):
        if r["source_id"] not in known:
            continue
        t = sort_time(r["published"], r["first_seen"])
        if t < min_time:
            continue
        out.append({"id": r["id"], "s": r["source_id"], "t": t, "fs": r["first_seen"],
                    "title": r["title"], "link": r["link"], "summary": r["summary"] or ""})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out


def aggregate_geo(sources, items, countries, since):
    min_time = time.time() - KEEP_HOURS * 3600
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


def geo_payload(since):
    cutoff = time.time() - KEEP_HOURS * 3600 - 3600
    rows = STORE.items(max(since, cutoff))
    out = aggregate_geo(FETCHER.sources, rows, load_countries(), since)
    out["updated"] = FETCHER.last_cycle
    return out


def export_markdown(only_undigested):
    rows = STORE.saved()
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
        safe = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if safe.startswith("..") or ":" in safe:
            return self._json({"error": "not found"}, 404)
        full = os.path.join(BASE_DIR, "static", *safe.split("/"))
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        path = url.path
        try:
            if path in ("/", "/index.html"):
                with open(INDEX_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path.startswith("/js/") and path.endswith(".js"):
                self._serve_static(path, "text/javascript; charset=utf-8")
            elif path == "/api/status":
                self._json(status_payload())
            elif path == "/api/items":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    since = 0.0
                payload = status_payload()
                payload["items"] = items_payload(since)
                self._json(payload)
            elif path == "/api/geo":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    since = 0.0
                self._json(geo_payload(since))
            elif path == "/api/sources":
                payload = status_payload()
                payload["sources"] = source_payload(FETCHER.sources, STORE)
                self._json(payload)
            elif path == "/api/saved":
                self._json({"saved": STORE.saved()})
            elif path == "/api/export.md":
                only = (q.get("only") or [""])[0] == "undigested"
                extra = {}
                if (q.get("download") or [""])[0]:
                    extra["Content-Disposition"] = (
                        'attachment; filename="news-central-saved-%s.md"' % time.strftime("%Y-%m-%d"))
                self._send(200, export_markdown(only).encode("utf-8"), "text/markdown; charset=utf-8", extra)
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
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
            if path == "/api/refresh":
                self._json({"started": FETCHER.refresh_now()})
            elif path == "/api/save":
                item = STORE.get_item(item_id)
                if not item:
                    return self._json({"error": "item not found"}, 404)
                STORE.save(item, FETCHER.source_map().get(item["source_id"], {}))
                self._json({"ok": True})
            elif path == "/api/unsave":
                STORE.unsave(item_id)
                self._json({"ok": True})
            elif path == "/api/saved/update":
                STORE.update_saved(item_id, body.get("note"), body.get("digested"))
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json({"error": str(e)}, 500)


# ---------------------------------------------------------------- entry points

class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second copy bind the same port, so the
    # "already running" check would never fire. Elsewhere it only skips TIME_WAIT.
    allow_reuse_address = os.name != "nt"


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def run_check():
    sources = load_sources()
    print("Checking %d sources…" % len(sources), flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda s: (s, fetch_source(s, None)), sources))
    results.sort(key=lambda x: (x[1]["status"] != "error", x[0].get("region", ""), x[0].get("name", "")))
    counts = {}
    for s, r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        name = s.get("name", s["id"])[:34]
        if r["status"] == "error":
            print("FAIL   %-34s %s" % (name, r["error"]))
        elif r["status"] == "discovered":
            print("FOUND  %-34s %3d items  %s" % (name, len(r["items"]), r["feed"]))
        else:
            print("OK     %-34s %3d items" % (name, len(r["items"])))
    print("\n%d ok · %d found by auto-discovery · %d failing" % (
        counts.get("ok", 0), counts.get("discovered", 0), counts.get("error", 0)))
    return 0


def main():
    global STORE, FETCHER
    ap = argparse.ArgumentParser(description="News Central: local world-news reader")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--lan", action="store_true", help="also serve to other devices on this Wi-Fi")
    ap.add_argument("--open", action="store_true", help="open the reader in the browser after starting")
    ap.add_argument("--check", action="store_true", help="test every source once, print a report and exit")
    ap.add_argument("--check-sources", action="store_true", help="validate source data model and exit")
    args = ap.parse_args()
    if args.check:
        return run_check()
    if args.check_sources:
        errs = validate_sources(load_sources(), load_countries())
        for e in errs:
            print("FAIL", e)
        print("%d problem(s)" % len(errs))
        return 1 if errs else 0

    STORE = Store(DB_FILE)
    FETCHER = Fetcher(STORE)
    url = "http://127.0.0.1:%d" % args.port
    try:
        httpd = Server(("0.0.0.0" if args.lan else "127.0.0.1", args.port), Handler)
    except OSError:
        print("Port %d is busy; News Central is probably already running at %s" % (args.port, url))
        if args.open:
            webbrowser.open(url)
        return 1
    FETCHER.start()
    print("News Central is running at", url)
    if args.lan:
        ip = lan_ip()
        if ip:
            print("From your phone on the same Wi-Fi: http://%s:%d" % (ip, args.port))
    print("Keep this window open while you read. Close it (or press Ctrl+C) to stop.", flush=True)
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
