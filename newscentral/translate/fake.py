# A deterministic engine for tests, development and browser checks. Never used for real reading.
# Markers in the input simulate engine faults: EMPTY -> empty output, LONG -> six times the input,
# CRASH -> the whole call raises.
from .base import Engine


class FakeEngine(Engine):
    name = "fake"

    def __init__(self, model_dir, threads=1):
        super().__init__(model_dir, threads)
        self.version = "1"

    @classmethod
    def model_ready(cls, model_dir):
        return True

    def supports(self, src, tgt):
        return bool(src) and src != tgt

    def translate(self, texts, src, tgt):
        out = []
        for t in texts:
            if "CRASH" in t:
                raise RuntimeError("fake engine crash")
            if "EMPTY" in t:
                out.append("")
            elif "LONG" in t:
                out.append(" ".join([t] * 6))
            else:
                out.append("[%s>%s] %s" % (src, tgt, t))
        return out
