# newscentral/config.py
# Paths, tuning constants and config.json loading. No imports from the rest of the package.
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
COUNTRIES_FILE = os.path.join(BASE_DIR, "geo", "countries.json")
PRESS_FREEDOM_FILE = os.path.join(BASE_DIR, "geo", "press-freedom-2026.json")
LANGUAGES_FILE = os.path.join(BASE_DIR, "geo", "languages.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")

REFRESH_SECONDS = 900          # background fetch every 15 minutes
ERROR_RETRY_SECONDS = 3600     # failing sources retried hourly, or on manual refresh
LIVE_WINDOW_HOURS = 72         # how far back the live feed and the map look; the archive keeps everything
LIVE_ROW_LIMIT = 30000         # rows per /api/items response; beyond it the response says "truncated"
FETCH_TIMEOUT = 20
WORKERS = 16
MAX_ITEMS_PER_FEED = 60
MAX_BYTES = 12 * 1024 * 1024
SUMMARY_CHARS = 500
TARGET_LANG = "es"             # the one language translations are written in

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    "port": 8787,
    "listen": "127.0.0.1",
    "data_dir": "data",
    "read_langs": ["es", "en"],
    "refresh_minutes": 15,
    "keep_awake": False,
    "log_file": None,
    "worker": True,
    "translate_batch": 16,
    "worker_threads": None,
    "translate_engine": {"default": "nllb-600m"},
    "backup_dir": None,
}


class ConfigError(Exception):
    pass


def _check(key, value):
    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if key == "port":
        ok = is_int(value) and 1 <= value <= 65535
    elif key == "refresh_minutes":
        ok = is_int(value) and value >= 1
    elif key in ("listen", "data_dir"):
        ok = isinstance(value, str) and bool(value)
    elif key == "read_langs":
        ok = isinstance(value, list) and bool(value) and all(isinstance(x, str) for x in value)
    elif key in ("keep_awake", "worker"):
        ok = isinstance(value, bool)
    elif key == "translate_batch":
        ok = is_int(value) and 1 <= value <= 256
    elif key == "worker_threads":
        ok = value is None or (is_int(value) and value >= 1)
    elif key == "translate_engine":
        ok = (isinstance(value, dict) and isinstance(value.get("default"), str)
              and all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in value.items()))
    elif key in ("log_file", "backup_dir"):
        ok = value is None or (isinstance(value, str) and bool(value))
    else:
        raise ConfigError("config: unknown key %r" % key)
    if not ok:
        raise ConfigError("config: invalid value for %r: %r" % (key, value))


def load_config(path=CONFIG_FILE, overrides=None):
    """Defaults, then the config file, then overrides (command-line flags). None overrides are ignored."""
    cfg = dict(DEFAULTS)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8-sig") as f:   # utf-8-sig: PowerShell writes a BOM
                data = json.load(f)
        except ValueError as e:
            raise ConfigError("config: %s is not valid JSON: %s" % (path, e))
        if not isinstance(data, dict):
            raise ConfigError("config: %s must contain a JSON object" % path)
        for key, value in data.items():
            _check(key, value)
            cfg[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            _check(key, value)
            cfg[key] = value
    return cfg


def worker_threads(cfg):
    """CPU threads an engine may use. Default: half the cores, at least one, so the reader and
    the fetcher stay responsive."""
    return cfg.get("worker_threads") or max(1, (os.cpu_count() or 2) // 2)


def paths(cfg):
    def resolve(p):
        return p if os.path.isabs(p) else os.path.join(BASE_DIR, *p.replace("\\", "/").split("/"))

    data_dir = resolve(cfg["data_dir"])
    return {
        "data": data_dir,
        "db": os.path.join(data_dir, "news.db"),
        "status": os.path.join(data_dir, "sources_status.json"),
        "models": os.path.join(data_dir, "models"),
        "bench": os.path.join(data_dir, "bench"),
        "log": resolve(cfg["log_file"]) if cfg.get("log_file") else None,
        "backup": resolve(cfg["backup_dir"]) if cfg.get("backup_dir") else None,
    }
