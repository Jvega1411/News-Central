# The interface every translation engine implements. Engines receive canonical text (from items)
# and return plain text; the job canonicalizes and checks the output. Heavy dependencies are
# imported inside the engine, never at module level, so this package imports without them.
import importlib.util
import json
import os

SPECIAL_TOKENS = ("</s>", "<s>", "<pad>")


class Engine:
    name = ""
    requires = ()          # importable module names the engine needs

    def __init__(self, model_dir, threads=1):
        self.model_dir = model_dir
        self.threads = max(1, int(threads))
        self.version = read_version(model_dir)

    @classmethod
    def missing_modules(cls):
        return [m for m in cls.requires if importlib.util.find_spec(m) is None]

    @classmethod
    def model_ready(cls, model_dir):
        return os.path.isfile(os.path.join(model_dir, "model.bin"))

    @classmethod
    def check(cls, model_dir):
        """Empty string when the engine can run, otherwise the reason it cannot."""
        missing = cls.missing_modules()
        if missing:
            return ("missing Python packages: %s (py -m pip install -r requirements-translate.txt)"
                    % ", ".join(missing))
        if not cls.model_ready(model_dir):
            return "model not downloaded (py tools/get_models.py %s)" % cls.name
        return ""

    def supports(self, src, tgt):
        raise NotImplementedError

    def translate(self, texts, src, tgt):
        """One output string per input string, in order."""
        raise NotImplementedError


def read_version(model_dir):
    """The model revision recorded by tools/get_models.py in <model_dir>/source.json."""
    try:
        with open(os.path.join(model_dir, "source.json"), encoding="utf-8") as f:
            meta = json.load(f)
        return str(meta.get("revision") or "unknown")[:12]
    except (OSError, ValueError, AttributeError):
        return "unknown"


def strip_special(tokens):
    return [t for t in tokens if t not in SPECIAL_TOKENS]
