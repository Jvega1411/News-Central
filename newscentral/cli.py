import argparse
import os
import socket
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from .api import App, Handler, Server
from .config import CONFIG_FILE, ConfigError, WORKERS, load_config, paths
from .feeds import fetch_source
from .fetcher import Fetcher
from .sources import load_countries, load_languages, load_sources, validate_sources
from .store import Store


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


def setup_log(path, max_bytes=5 * 1024 * 1024):
    """Send stdout and stderr to a file. Needed under pythonw, which has no console.
    A file over max_bytes is moved to <path>.1 first (one previous file is kept)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except OSError:
        pass
    stream = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    return stream


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake(on):
    """Ask Windows not to sleep on idle while this thread is alive (on=True), or withdraw the
    request (on=False). Call it from the thread that runs the server. It does not override the
    lid-close action: tools/windows/install.ps1 sets that. Returns True when Windows accepted."""
    if os.name != "nt":
        if on:
            print("keep_awake is only supported on Windows; ignored", flush=True)
        return False
    import ctypes
    fn = ctypes.windll.kernel32.SetThreadExecutionState
    fn.argtypes = [ctypes.c_uint]
    fn.restype = ctypes.c_uint
    return bool(fn(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)))


def main():
    ap = argparse.ArgumentParser(description="News Central: local world-news reader")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--lan", action="store_true", help="also serve to other devices on this Wi-Fi")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    ap.add_argument("--open", action="store_true", help="open the reader in the browser after starting")
    ap.add_argument("--check", action="store_true", help="test every source once, print a report and exit")
    ap.add_argument("--check-sources", action="store_true", help="validate source data model and exit")
    args = ap.parse_args()
    if args.check:
        return run_check()
    if args.check_sources:
        errs = validate_sources(load_sources(), load_countries(), load_languages())
        for e in errs:
            print("FAIL", e)
        print("%d problem(s)" % len(errs))
        return 1 if errs else 0

    try:
        cfg = load_config(args.config, {"port": args.port, "listen": "0.0.0.0" if args.lan else None})
    except ConfigError as e:
        print(e)
        return 2
    p = paths(cfg)
    if p["log"]:
        setup_log(p["log"])
    lan = cfg["listen"] != "127.0.0.1"
    store = Store(p["db"])
    fetcher = Fetcher(store, cfg["refresh_minutes"] * 60, p["status"])
    url = "http://127.0.0.1:%d" % cfg["port"]
    try:
        httpd = Server((cfg["listen"], cfg["port"]), Handler)
    except OSError:
        print("Port %d is busy; News Central is probably already running at %s" % (cfg["port"], url))
        if args.open:
            webbrowser.open(url)
        return 1
    httpd.app = App(store, fetcher, cfg)
    fetcher.start()
    print("News Central is running at", url)
    if lan:
        ip = lan_ip()
        if ip:
            print("From your phone on the same Wi-Fi: http://%s:%d" % (ip, cfg["port"]))
    print("Keep this window open while you read. Close it (or press Ctrl+C) to stop.", flush=True)
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    awake = cfg["keep_awake"] and keep_awake(True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if awake:
            keep_awake(False)
    return 0
