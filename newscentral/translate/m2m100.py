# newscentral/translate/m2m100.py
# M2M100 418M on CTranslate2. MIT licence. The downloaded conversion is float16; ct2.translator
# runs it as int8.
import os

from . import ct2
from .base import Engine, strip_special

# Languages of M2M100 (model card of facebook/m2m100_418M), ISO 639-1 where one exists.
M2M_LANGS = set((
    "af am ar ast az ba be bg bn br bs ca ceb cs cy da de el en es et fa ff fi fr fy ga gd gl gu ha he "
    "hi hr ht hu hy id ig ilo is it ja jv ka kk km kn ko lb lg ln lo lt lv mg mk ml mn mr ms my ne nl "
    "no ns oc or pa pl ps pt ro ru sd si sk sl so sq sr ss su sv sw ta th tl tn tr uk ur uz vi wo xh "
    "yi yo zh zu").split())


def lang_token(lang):
    return "__%s__" % lang


def source_tokens(pieces, src):
    return [lang_token(src)] + list(pieces) + ["</s>"]


class M2M100Engine(Engine):
    name = "m2m100-418m"
    requires = ("ctranslate2", "sentencepiece")

    def __init__(self, model_dir, threads=1):
        super().__init__(model_dir, threads)
        self._translator = None
        self._sp = None

    def _load(self):
        if self._sp is None:
            self._sp = ct2.sentencepiece(os.path.join(self.model_dir, "sentencepiece.bpe.model"))
        if self._translator is None:
            self._translator = ct2.translator(self.model_dir, self.threads)

    def supports(self, src, tgt):
        return src != tgt and src in M2M_LANGS and tgt in M2M_LANGS

    def translate(self, texts, src, tgt):
        if not texts:
            return []
        self._load()
        source = [source_tokens(self._sp.encode(t, out_type=str), src) for t in texts]
        results = self._translator.translate_batch(
            source, target_prefix=[[lang_token(tgt)]] * len(source), beam_size=ct2.BEAM_SIZE,
            max_decoding_length=ct2.max_length(source))
        return [self._sp.decode(strip_special(r.hypotheses[0][1:])) for r in results]
