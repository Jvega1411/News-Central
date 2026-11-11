import codecs
import html
import re
import unicodedata

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
APPEARED_RE = re.compile(r"\s*The post .{1,300}? appeared first on .{1,200}?\.?\s*$", re.S)
SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
# C0 and C1 controls except tab, LF, VT, FF, CR and NEL, which count as whitespace.
CONTROL_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x84\x86-\x9f]")
JOINERS = "\u200c\u200d"   # zero-width non-joiner and joiner: meaningful in Persian and Indic scripts
_REMOVE = dict.fromkeys(map(ord, "\ufeff\u00ad\u200b\u2060\u200e\u200f"
                                 "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))
_REMOVE[0x2028] = " "
_REMOVE[0x2029] = " "


def strip_markup(value):
    s = SCRIPT_RE.sub(" ", value)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(html.unescape(s))
    s = TAG_RE.sub(" ", s)
    return APPEARED_RE.sub("", s)


def _truncate(s, limit):
    """Cut to at most `limit` characters including the ellipsis."""
    cut = s[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    else:
        i = len(cut)   # s[i] is the first character left out
        while i > 0 and (unicodedata.category(s[i])[0] == "M" or s[i] in JOINERS or s[i - 1] in JOINERS):
            i -= 1
        cut = s[:i]
    return cut.rstrip(" ,;:.") + "\u2026"


def canonical(value, limit=None):
    """The Unicode half of normalize(): NFC, no invisible characters, single spaces. Safe to re-apply."""
    if not value:
        return ""
    s = unicodedata.normalize("NFC", value)
    s = s.translate(_REMOVE)
    s = CONTROL_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        s = _truncate(s, limit)
    return s


def normalize(value, limit=None):
    """The only path by which feed text reaches the database."""
    if not value:
        return ""
    return canonical(strip_markup(value), limit)


XML_ENCODING_RE = re.compile(rb'encoding=["\']([A-Za-z0-9_.\-]+)["\']')
CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.\-]+)", re.I)
LATIN_FALLBACKS = ("cp1252", "latin-1")
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))


class UndecodableFeed(ValueError):
    pass


def charset_from_content_type(value):
    m = CHARSET_RE.search(value or "")
    return m.group(1) if m else None


def decode_bytes(data, http_charset=None, latin_fallback=True):
    """Order: byte-order mark, XML declaration, UTF-8, HTTP charset, then (Latin-script sources only)
    cp1252 and latin-1. UTF-8 goes before the HTTP charset because servers often announce
    ISO-8859-1 for feeds that are really UTF-8, and latin-1 decodes anything without error."""
    candidates = []
    for bom, enc in _BOMS:
        if data.startswith(bom):
            candidates.append(enc)
            break
    m = XML_ENCODING_RE.search(data[:300])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    if http_charset:
        candidates.append(http_charset)
    if latin_fallback:
        candidates.extend(LATIN_FALLBACKS)
    for enc in candidates:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    raise UndecodableFeed("undecodable feed (no usable charset)")
