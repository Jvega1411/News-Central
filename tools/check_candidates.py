#!/usr/bin/env python3
# Fetch candidate sources once and print a review table. Writes nothing.
# Usage: py tools/check_candidates.py candidates.json
# candidates.json is a JSON list of source objects in the sources.json format.
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import SOURCES_FILE                                          # noqa: E402
from newscentral.feeds import fetch_source                                           # noqa: E402
from newscentral.sources import load_countries, load_languages, validate_sources     # noqa: E402


def existing_ids():
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("sources", []) if isinstance(data, dict) else data
    return {str(s.get("id")) for s in raw}          # disabled sources count too


def main(path):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with open(path, encoding="utf-8") as f:
        candidates = json.load(f)
    problems = validate_sources(candidates, load_countries(), load_languages())
    taken, seen = existing_ids(), set()
    for s in candidates:
        if s.get("id") in taken:
            problems.append("%s: id already exists in sources.json" % s.get("id"))
        if s.get("id") in seen:
            problems.append("%s: id repeated in the candidate file" % s.get("id"))
        seen.add(s.get("id"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: fetch_source(s, None), candidates))
    print("%-6s %-24s %-4s %-18s %-9s %5s  %s" % ("STATE", "ID", "LANG", "PLACE", "KIND", "ITEMS", "SAMPLE / ERROR"))
    ok = 0
    for s, r in zip(candidates, results):
        place = s.get("country_iso") or s.get("scope") or "?"
        if s.get("exile"):
            place += ">" + str(s.get("covers"))
        flags = ("S" if s.get("state_media") else "") + ("P" if s.get("paywall") else "")
        if r["status"] == "error":
            state, detail = "FAIL", r["error"]
        else:
            ok += 1
            state = "OK" if r["status"] == "ok" else "FOUND"
            detail = r["items"][0]["title"][:70]
            if r["status"] == "discovered":
                detail += "  [feed: %s]" % r["feed"]
        print("%-6s %-24s %-4s %-18s %-9s %5d  %s" % (
            state, str(s.get("id"))[:24], s.get("lang", "?"), (place + (" " + flags if flags else ""))[:18],
            s.get("kind") or "news", len(r["items"]), detail))
    print("\n%d of %d working" % (ok, len(candidates)))
    for p in problems:
        print("PROBLEM", p)
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__ or "usage: check_candidates.py candidates.json")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
