#!/usr/bin/env python3
# Put translated items back in the translation queue. Safe while the server runs (WAL): the
# worker picks them up on its next loop. Run it after changing "translate_engine" in config.json.
# Usage: py tools/retranslate.py --engine nllb-600m [--lang zh]   delete that engine's translations
#        py tools/retranslate.py --failures [--lang zh]           only clear failure counts
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import CONFIG_FILE, ConfigError, TARGET_LANG, load_config, paths   # noqa: E402
from newscentral.store import SchemaMismatch, Store                                        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Put translations back in the queue")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--engine", help="delete the translations made by this engine (and clear failures)")
    what.add_argument("--failures", action="store_true", help="only clear the failure counts")
    ap.add_argument("--lang", help="only items whose original language is this code")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(e)
        return 2
    try:
        store = Store(paths(cfg)["db"], migrate=False)
    except (SchemaMismatch, FileNotFoundError) as e:
        print(e)
        return 1
    try:
        deleted, cleared = store.clear_translations(args.engine, args.lang, TARGET_LANG)
    finally:
        store.db.close()
    print("%d translation(s) deleted, %d failure record(s) cleared" % (deleted, cleared))
    return 0


if __name__ == "__main__":
    sys.exit(main())
