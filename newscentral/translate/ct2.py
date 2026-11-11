# newscentral/translate/ct2.py
# Lazy loaders for the optional dependencies (requirements-translate.txt). Nothing here is
# imported until an engine translates for the first time.
BEAM_SIZE = 2


def translator(model_dir, threads):
    import ctranslate2
    # int8 on the CPU whatever the stored precision: CTranslate2 quantizes float16 models at load.
    return ctranslate2.Translator(model_dir, device="cpu", compute_type="int8",
                                  inter_threads=1, intra_threads=threads)


def sentencepiece(path):
    import sentencepiece as spm
    return spm.SentencePieceProcessor(model_file=path)


def max_length(token_lists):
    """Decoding limit: generous for headlines and 500-character summaries, bounded for runaways."""
    return min(512, 3 * max(len(t) for t in token_lists) + 16)
