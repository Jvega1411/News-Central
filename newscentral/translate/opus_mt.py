# newscentral/translate/opus_mt.py
# Opus-MT (Marian) on CTranslate2: one model per language pair, under <model_dir>/<pair>/.
# routes.json (written by tools/get_models.py) lists, per source language, the pairs to chain:
# ["fr-es"] for a direct model, ["zh-en", "en-es"] for a pivot through English.
import json
import os

from ..config import TARGET_LANG
from . import ct2
from .base import Engine, strip_special

ROUTES_FILE = "routes.json"


def source_tokens(pieces):
    return list(pieces) + ["</s>"]


def load_routes(model_dir):
    try:
        with open(os.path.join(model_dir, ROUTES_FILE), encoding="utf-8") as f:
            routes = json.load(f)
        return {k: list(v) for k, v in routes.items()} if isinstance(routes, dict) else {}
    except (OSError, ValueError):
        return {}


class OpusMtEngine(Engine):
    name = "opus-mt"
    requires = ("ctranslate2", "sentencepiece")

    def __init__(self, model_dir, threads=1):
        super().__init__(model_dir, threads)
        self.routes = load_routes(model_dir)
        self._pairs = {}           # pair -> (translator, source pieces, target pieces)

    @classmethod
    def model_ready(cls, model_dir):
        return os.path.isfile(os.path.join(model_dir, ROUTES_FILE))

    def pair_ready(self, pair):
        return os.path.isfile(os.path.join(self.model_dir, pair, "model.bin"))

    def supports(self, src, tgt):
        route = self.routes.get(src)
        return tgt == TARGET_LANG and bool(route) and all(self.pair_ready(p) for p in route)

    def _pair(self, pair):
        if pair not in self._pairs:
            folder = os.path.join(self.model_dir, pair)
            self._pairs[pair] = (ct2.translator(folder, self.threads),
                                 ct2.sentencepiece(os.path.join(folder, "source.spm")),
                                 ct2.sentencepiece(os.path.join(folder, "target.spm")))
        return self._pairs[pair]

    def _run(self, pair, texts):
        translator, sp_source, sp_target = self._pair(pair)
        source = [source_tokens(sp_source.encode(t, out_type=str)) for t in texts]
        results = translator.translate_batch(source, beam_size=ct2.BEAM_SIZE,
                                             max_decoding_length=ct2.max_length(source))
        return [sp_target.decode(strip_special(r.hypotheses[0])) for r in results]

    def translate(self, texts, src, tgt):
        if not texts:
            return []
        current = list(texts)
        for pair in self.routes[src]:
            current = self._run(pair, current)
        return current
