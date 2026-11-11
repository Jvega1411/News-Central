# newscentral/config.py
# Paths and tuning constants. No imports from the rest of the package.
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
COUNTRIES_FILE = os.path.join(BASE_DIR, "geo", "countries.json")
PRESS_FREEDOM_FILE = os.path.join(BASE_DIR, "geo", "press-freedom-2026.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "news.db")
STATUS_FILE = os.path.join(DATA_DIR, "sources_status.json")

REFRESH_SECONDS = 900          # background fetch every 15 minutes
ERROR_RETRY_SECONDS = 3600     # failing sources retried hourly, or on manual refresh
KEEP_HOURS = 96                # renamed LIVE_WINDOW_HOURS in Task 6
FETCH_TIMEOUT = 20
WORKERS = 16
MAX_ITEMS_PER_FEED = 60
MAX_BYTES = 12 * 1024 * 1024
SUMMARY_CHARS = 500
