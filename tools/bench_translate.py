#!/usr/bin/env python3
# Translation benchmark. Reads the archive through the database the server owns (WAL, safe
# while it runs; stop the worker with --no-worker first so it does not compete for the CPU).
# Usage: py tools/bench_translate.py run [--engines nllb-600m,opus-mt] [--per-lang 15]
#        py tools/bench_translate.py summarize [--date YYYY-MM-DD]
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import CONFIG_FILE, ConfigError, load_config, paths, worker_threads   # noqa: E402
from newscentral.store import SchemaMismatch, Store                                          # noqa: E402
from newscentral.translate import bench, registry                                            # noqa: E402


def run(cfg, p, args):
    try:
        store = Store(p["db"], migrate=False)
    except (SchemaMismatch, FileNotFoundError) as e:
        print(e)
        return 1
    try:
        units = bench.sample_units(store, cfg["read_langs"], args.per_lang)
    finally:
        store.db.close()
    names = [n for n in args.engines.split(",") if n] if args.engines else registry.installed(p["models"])
    if not names:
        print("No translation engine is installed. See requirements-translate.txt and tools/get_models.py.")
        return 1
    for name in names:
        reason = registry.check(name, p["models"])
        if reason:
            print("%s: %s" % (name, reason))
            return 1
    print("%d units in %d languages; engines: %s" % (len(units), len({u["lang"] for u in units}), ", ".join(names)))
    engines = {n: registry.create(n, p["models"], worker_threads(cfg)) for n in names}
    data = bench.run_bench(units, engines, cfg["translate_batch"])
    path = bench.bench_path(p["bench"], data["date"])
    bench.write_json(path, data)
    for name, info in sorted(data["engines"].items()):
        print("%-14s version %-12s errors %4d  s/title %s  s/summary %s" % (
            name, info["version"], info["errors"], info["seconds_per_item"]["title"],
            info["seconds_per_item"]["summary"]))
    print("Wrote", path)
    return 0


def summarize(p, args):
    dates = bench.list_dates(p["bench"])
    date = args.date or (dates[-1] if dates else "")
    if date not in dates:
        print("No benchmark found in", p["bench"])
        return 1
    data, scores, review = bench.load_round(p["bench"], date)
    if not scores:
        print("No scores yet: %s is missing" % bench.bench_path(p["bench"], date, "scores"))
        return 1
    out = bench.summarize(data, scores)
    print("%-5s %-14s %9s %8s %9s %5s" % ("LANG", "ENGINE", "ADEQUACY", "FLUENCY", "S/ITEM", "N"))
    for lang, rows in out["by_lang"].items():
        for name, r in sorted(rows.items()):
            mark = " <" if out["choice"].get(lang) == name else ""
            print("%-5s %-14s %9s %8s %9s %5d%s" % (lang, name, r["adequacy"], r["fluency"], r["seconds"], r["n"], mark))
    disagreed = sum(1 for k in review.values() for v in k.values() if v.get("disagree"))
    reviewed = sum(len(k) for k in review.values())
    print("Review: %d of %d checked scores marked as wrong" % (disagreed, reviewed))
    print('Put this in config.json: "translate_engine": %s' % json.dumps(out["translate_engine"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Translation benchmark")
    sub = ap.add_subparsers(dest="command")
    r = sub.add_parser("run")
    r.add_argument("--engines", default="", help="comma-separated engine names (default: every installed one)")
    r.add_argument("--per-lang", type=int, default=15)
    r.add_argument("--config", default=CONFIG_FILE)
    s = sub.add_parser("summarize")
    s.add_argument("--date", default="")
    s.add_argument("--config", default=CONFIG_FILE)
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(e)
        return 2
    p = paths(cfg)
    return run(cfg, p, args) if args.command == "run" else summarize(p, args)


if __name__ == "__main__":
    sys.exit(main())
