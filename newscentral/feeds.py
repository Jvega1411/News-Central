import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.entities import name2codepoint

from .config import MAX_ITEMS_PER_FEED, SUMMARY_CHARS
from .http_client import NotModified, get_with_fallback, short_error
from .text import decode_bytes, normalize


XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?", re.I)
LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "ocid", "cmpid", "mc_cid", "mc_eid")


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
    title = normalize(child_text(el, "title"), 300)
    link = entry_link(el, base)
    if not title or not link.startswith(("http://", "https://")):
        return None
    summary = normalize(child_text(el, "description", "summary", "encoded", "content"), SUMMARY_CHARS)
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

        title = normalize(grab(r"<title[^>]*>(.*?)</title>"), 300)
        link = html.unescape(grab(r"<link[^>]*href=[\"']([^\"']+)[\"']")
                             or grab(r"<link[^>]*>(.*?)</link>")
                             or grab(r"<guid[^>]*>(https?://.*?)</guid>"))
        link = urllib.parse.urljoin(base, link) if link else ""
        if not title or not link.startswith(("http://", "https://")):
            continue
        out.append({
            "title": title,
            "link": normalize_link(link),
            "summary": normalize(grab(r"<(description|summary)[^>]*>(.*?)</\1>"), SUMMARY_CHARS),
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
