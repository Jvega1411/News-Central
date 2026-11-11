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
