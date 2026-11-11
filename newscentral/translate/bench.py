# newscentral/translate/bench.py
# Translation benchmark: sample archived items, run every engine, record text and seconds per
# item, then summarize Claude's scores into one engine per source language.
# Files in <data_dir>/bench/: translate-<date>.json (this module), translate-<date>-scores.json
# (written by Claude in a Claude Code session), translate-<date>-review.json (the review page).
import hashlib
import json
import os
import platform
import re
import time

from ..config import TARGET_LANG
from ..text import canonical
from .job import check_output

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_RE = re.compile(r"^translate-(\d{4}-\d{2}-\d{2})\.json$")
REVIEW_SAMPLE = 25


def bench_path(bench_dir, date, kind=""):
    if not DATE_RE.match(date or ""):
        raise ValueError("date must be YYYY-MM-DD")
    return os.path.join(bench_dir, "translate-%s%s.json" % (date, "-" + kind if kind else ""))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def sample_units(store, read_langs, per_lang=15, target=TARGET_LANG):
    units = []
    for r in store.sample_by_lang(list(read_langs) + [target], per_lang):
        for field in ("title", "summary"):
            text = r[field] or ""
            if text:
                units.append({"key": "%s|%s" % (r["id"], field), "item_id": r["id"], "source_id": r["source_id"],
                              "lang": r["lang"], "field": field, "text": text, "first_seen": r["first_seen"]})
    return units


def run_bench(units, engines, batch_size=16, clock=time.perf_counter, date=None):
    """engines: {name: Engine}. Every engine translates every unit it supports, in batches per
    (language, field); seconds per item = batch time / batch size."""
    items = [dict(u, translations={}) for u in units]
    groups = {}
    for it in items:
        groups.setdefault((it["lang"], it["field"]), []).append(it)
    summary = {}
    for name in sorted(engines):
        engine = engines[name]
        times = {"title": [], "summary": []}
        errors = 0
        for lang, field in sorted(groups):
            group = groups[(lang, field)]
            for start in range(0, len(group), batch_size):
                chunk = group[start:start + batch_size]
                if not engine.supports(lang, TARGET_LANG):
                    for it in chunk:
                        it["translations"][name] = {"text": "", "seconds": None, "error": "unsupported"}
                    continue
                began = clock()
                try:
                    outputs = engine.translate([it["text"] for it in chunk], lang, TARGET_LANG)
                    failure = ""
                except Exception as e:
                    outputs = [""] * len(chunk)
                    failure = ("%s: %s" % (type(e).__name__, e))[:300]
                per = (clock() - began) / len(chunk)
                for it, output in zip(chunk, outputs):
                    text, error = check_output(it["text"], output)
                    error = failure or error
                    if error:
                        errors += 1
                        text = canonical(output or "")
                    else:
                        times[field].append(per)
                    it["translations"][name] = {"text": text, "seconds": round(per, 4), "error": error}
        summary[name] = {"version": engine.version, "errors": errors,
                         "seconds_per_item": {f: (round(sum(v) / len(v), 4) if v else None)
                                              for f, v in times.items()}}
    return {"date": date or time.strftime("%Y-%m-%d"), "machine": platform.platform(),
            "python": platform.python_version(), "target": TARGET_LANG, "engines": summary, "items": items}


def _mean(values):
    return round(sum(values) / len(values), 3) if values else None


def compress(choice):
    """{lang: engine} to a translate_engine config value: the most chosen engine as default."""
    if not choice:
        return {}
    counts = {}
    for engine in choice.values():
        counts[engine] = counts.get(engine, 0) + 1
    default = sorted(counts, key=lambda e: (-counts[e], e))[0]
    out = {"default": default}
    for lang in sorted(choice):
        if choice[lang] != default:
            out[lang] = choice[lang]
    return out


def summarize(data, scores):
    """Mean adequacy, fluency and seconds per engine and source language. An engine that failed on
    an item scores 1 on it; an engine that does not support a language is not a candidate for it.
    Choice: highest mean adequacy (one decimal), then fastest, then name."""
    table = {}
    for it in data["items"]:
        for name, tr in it["translations"].items():
            if tr["error"] == "unsupported":
                continue
            acc = table.setdefault(it["lang"], {}).setdefault(name, {"adequacy": [], "fluency": [], "seconds": []})
            if tr["error"]:
                acc["adequacy"].append(1)
                acc["fluency"].append(1)
                continue
            s = (scores.get(it["key"]) or {}).get(name)
            if s:
                acc["adequacy"].append(s["adequacy"])
                acc["fluency"].append(s["fluency"])
            if tr["seconds"] is not None:
                acc["seconds"].append(tr["seconds"])
    by_lang, choice = {}, {}
    for lang in sorted(table):
        rows = {}
        for name, acc in table[lang].items():
            if acc["adequacy"]:
                rows[name] = {"adequacy": _mean(acc["adequacy"]), "fluency": _mean(acc["fluency"]),
                              "seconds": _mean(acc["seconds"]), "n": len(acc["adequacy"])}
        if rows:
            by_lang[lang] = rows
            choice[lang] = sorted(rows, key=lambda n: (-round(rows[n]["adequacy"], 1),
                                                       rows[n]["seconds"] if rows[n]["seconds"] is not None
                                                       else float("inf"), n))[0]
    return {"by_lang": by_lang, "choice": choice, "translate_engine": compress(choice)}


def list_dates(bench_dir):
    try:
        names = os.listdir(bench_dir)
    except OSError:
        return []
    return sorted(m.group(1) for m in (FILE_RE.match(n) for n in names) if m)


def load_round(bench_dir, date):
    """(benchmark, scores {key: {engine: {...}}}, review {key: {engine: {...}}})."""
    data = read_json(bench_path(bench_dir, date))
    if data is None:
        raise ValueError("no benchmark for %s" % date)
    scores = (read_json(bench_path(bench_dir, date, "scores"), {}) or {}).get("scores", {})
    review = (read_json(bench_path(bench_dir, date, "review"), {}) or {}).get("review", {})
    return data, scores, review


def review_sample(data, scores, n=REVIEW_SAMPLE):
    """A stable pseudo-random sample of scored items for the user to check."""
    keys = [it["key"] for it in data["items"] if it["key"] in scores]
    keys.sort(key=lambda k: hashlib.sha1(k.encode("utf-8")).hexdigest())
    return keys[:n]


def save_review(bench_dir, date, key, engine, disagree, note):
    path = bench_path(bench_dir, date, "review")
    data = read_json(path, None) or {"date": date, "review": {}}
    data.setdefault("review", {}).setdefault(key, {})[engine] = {"disagree": bool(disagree),
                                                                 "note": str(note or "")[:1000]}
    write_json(path, data)
