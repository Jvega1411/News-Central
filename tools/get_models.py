#!/usr/bin/env python3
# Download (or convert) a translation model into data/models/<engine>/. Models never enter git.
# Usage: py tools/get_models.py nllb-600m | m2m100-418m | opus-mt [--convert]
#   --convert builds the model locally from the original Transformers checkpoint with
#   ct2-transformers-converter (needs: py -m pip install transformers torch). For opus-mt it
#   also adds the direct it, tr, ja, ko, ru, uk and id models into Spanish, which nobody publishes
#   converted. Without it everything is downloaded ready to use.
# Sizes: nllb-600m about 0.6 GB, m2m100-418m about 1 GB, opus-mt about 2 to 3 GB (18 pairs).
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import CONFIG_FILE, ConfigError, load_config, paths            # noqa: E402
from newscentral.translate.download import ENGINE_NAMES, HUB, DownloadError, get       # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download a translation model")
    ap.add_argument("engine", choices=ENGINE_NAMES)
    ap.add_argument("--convert", action="store_true", help="convert locally instead of downloading")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    ap.add_argument("--hub", default=HUB, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(e)
        return 2
    try:
        dest = get(args.engine, paths(cfg)["models"], args.convert, args.hub)
    except DownloadError as e:
        print("Could not get %s: %s" % (args.engine, e))
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Run the same command again to resume; finished files are kept.")
        return 1
    print("Model ready in", dest)
    print("Check it with: py -m newscentral.translate.selfcheck", args.engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
