import html
import re

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
APPEARED_RE = re.compile(r"\s*The post .{1,300}? appeared first on .{1,200}?\.?\s*$", re.S)


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
