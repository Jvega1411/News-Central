# Engine names (as written in config.json "translate_engine" and in translations.engine) to classes.
# Each engine keeps its model under <data_dir>/models/<name>/.
import os

from .fake import FakeEngine
from .m2m100 import M2M100Engine
from .nllb import NllbEngine
from .opus_mt import OpusMtEngine

ENGINES = {
    "fake": FakeEngine,
    "nllb-600m": NllbEngine,
    "m2m100-418m": M2M100Engine,
    "opus-mt": OpusMtEngine,
}


def check(name, models_dir):
    cls = ENGINES.get(name)
    if cls is None:
        return "unknown engine %r (known: %s)" % (name, ", ".join(sorted(ENGINES)))
    return cls.check(os.path.join(models_dir, name))


def create(name, models_dir, threads=1):
    return ENGINES[name](os.path.join(models_dir, name), threads)


def installed(models_dir, include_fake=False):
    """Names of the engines that can run on this machine now."""
    return [n for n in sorted(ENGINES) if (include_fake or n != "fake") and not check(n, models_dir)]
