# newscentral/translate/selfcheck.py
# Translate three fixed sentences with one real engine and print the result and the time.
# Usage: py -m newscentral.translate.selfcheck nllb-600m | m2m100-418m | opus-mt [--config config.json]
import argparse
import sys
import time

from ..config import CONFIG_FILE, TARGET_LANG, load_config, paths, worker_threads
from ..text import canonical
from . import registry

SENTENCES = (
    ("fr", "La Banque centrale européenne a relevé ses taux d'intérêt."),
    ("ar", "ارتفع معدل التضخم "
           "في مصر"),
    ("zh", "中国政府宣布新的经济刺激计划"),
)


def selfcheck(name, models_dir, threads, out=print):
    reason = registry.check(name, models_dir)
    if reason:
        out("%s is unavailable: %s" % (name, reason))
        return 1
    engine = registry.create(name, models_dir, threads)
    out("%s, model version %s, %d thread(s)" % (name, engine.version, threads))
    bad = 0
    for src, text in SENTENCES:
        if not engine.supports(src, TARGET_LANG):
            out("%s: not supported by this engine" % src)
            continue
        start = time.perf_counter()
        result = engine.translate([text], src, TARGET_LANG)[0]
        out("%s -> %s (%.2f s): %s" % (src, TARGET_LANG, time.perf_counter() - start, result))
        if not canonical(result):
            bad += 1
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Translate three sentences with one engine")
    ap.add_argument("engine", choices=sorted(n for n in registry.ENGINES if n != "fake"))
    ap.add_argument("--config", default=CONFIG_FILE)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config(args.config)
    return selfcheck(args.engine, paths(cfg)["models"], worker_threads(cfg))


if __name__ == "__main__":
    sys.exit(main())
