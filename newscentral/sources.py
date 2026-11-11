import json

from .config import SOURCES_FILE, COUNTRIES_FILE, PRESS_FREEDOM_FILE, LANGUAGES_FILE


_COUNTRIES = None

def load_countries():
    global _COUNTRIES
    if _COUNTRIES is None:
        with open(COUNTRIES_FILE, encoding="utf-8") as f:
            _COUNTRIES = json.load(f)
    return _COUNTRIES


_PRESS_FREEDOM = None

def load_press_freedom():
    global _PRESS_FREEDOM
    if _PRESS_FREEDOM is None:
        try:
            with open(PRESS_FREEDOM_FILE, encoding="utf-8") as f:
                _PRESS_FREEDOM = json.load(f)
        except (OSError, ValueError):
            _PRESS_FREEDOM = {}
    return _PRESS_FREEDOM


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


_LANGUAGES = None

def load_languages():
    global _LANGUAGES
    if _LANGUAGES is None:
        with open(LANGUAGES_FILE, encoding="utf-8") as f:
            _LANGUAGES = json.load(f)
    return _LANGUAGES


def latin_script(lang):
    """True when the language is written in Latin script, or is unknown."""
    meta = load_languages().get(lang or "")
    return meta is None or meta["script"] == "Latn"


KIND_VOCAB = ("news", "analysis", "official")
SCOPE_VOCAB = ("Global", "Intergovernmental", "Transnational-regional")


def validate_sources(sources, countries, languages=None):
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
        if languages is not None:
            lang = s.get("lang")
            if lang not in languages:
                errors.append("%s: lang %r not in language reference" % (sid, lang))
        kind = s.get("kind")
        if kind is not None and kind not in KIND_VOCAB:
            errors.append("%s: kind %r not in %r" % (sid, kind, KIND_VOCAB))
    return errors


def country_label(s):
    countries = load_countries()
    iso = s.get("country_iso")
    if iso:
        name = countries.get(iso, {}).get("name", iso)
        if s.get("exile") and s.get("covers"):
            cov = countries.get(s["covers"], {}).get("name", s["covers"])
            return "%s (exile, covers %s)" % (name, cov)
        return name
    return s.get("scope") or ""
