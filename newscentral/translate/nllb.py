# newscentral/translate/nllb.py
# NLLB-200 distilled 600M on CTranslate2. One model for every language. CC-BY-NC 4.0 weights:
# fine for this personal, non-commercial use; they are downloaded, never committed.
import os

from . import ct2
from .base import Engine, strip_special

# ISO 639-1 (geo/languages.json) to NLLB-200 (FLORES-200) codes.
NLLB_CODES = {
    "am": "amh_Ethi", "ar": "arb_Arab", "bg": "bul_Cyrl", "bn": "ben_Beng", "ca": "cat_Latn",
    "cs": "ces_Latn", "da": "dan_Latn", "de": "deu_Latn", "el": "ell_Grek", "en": "eng_Latn",
    "es": "spa_Latn", "eu": "eus_Latn", "fa": "pes_Arab", "fi": "fin_Latn", "fr": "fra_Latn",
    "gl": "glg_Latn", "ha": "hau_Latn", "he": "heb_Hebr", "hi": "hin_Deva", "hu": "hun_Latn",
    "id": "ind_Latn", "it": "ita_Latn", "ja": "jpn_Jpan", "ko": "kor_Hang", "ms": "zsm_Latn",
    "nl": "nld_Latn", "no": "nob_Latn", "pl": "pol_Latn", "pt": "por_Latn", "ro": "ron_Latn",
    "ru": "rus_Cyrl", "sr": "srp_Cyrl", "sv": "swe_Latn", "sw": "swh_Latn", "ta": "tam_Taml",
    "th": "tha_Thai", "tr": "tur_Latn", "uk": "ukr_Cyrl", "ur": "urd_Arab", "vi": "vie_Latn",
    "zh": "zho_Hans",
}


def source_tokens(pieces, src):
    return [NLLB_CODES[src]] + list(pieces) + ["</s>"]


class NllbEngine(Engine):
    name = "nllb-600m"
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
        return src != tgt and src in NLLB_CODES and tgt in NLLB_CODES

    def translate(self, texts, src, tgt):
        if not texts:
            return []
        self._load()
        source = [source_tokens(self._sp.encode(t, out_type=str), src) for t in texts]
        results = self._translator.translate_batch(
            source, target_prefix=[[NLLB_CODES[tgt]]] * len(source), beam_size=ct2.BEAM_SIZE,
            max_decoding_length=ct2.max_length(source))
        return [self._sp.decode(strip_special(r.hypotheses[0][1:])) for r in results]
