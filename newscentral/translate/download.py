# newscentral/translate/download.py
# Where each engine's model comes from, and how to fetch it: plain urllib over the Hugging Face
# hub's file URLs (https://huggingface.co/<repo>/resolve/<revision>/<file>), or local conversion
# with CTranslate2's converter. Models live in <data_dir>/models/<engine>/ and never enter git.
import json
import os
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from ..http_client import SSL_CTX

HUB = "https://huggingface.co"
SKIP_FILES = (".gitattributes", "README.md")
ENGINE_NAMES = ("nllb-600m", "m2m100-418m", "opus-mt")

# Engines with one multilingual model.
SINGLE = {
    "nllb-600m": {"repo": "JustFrederik/nllb-200-distilled-600M-ct2-int8",
                  "convert_from": "facebook/nllb-200-distilled-600M",
                  "copy_files": ("sentencepiece.bpe.model",)},
    "m2m100-418m": {"repo": "michaelfeil/ct2fast-m2m100_418M",
                    "convert_from": "facebook/m2m100_418M",
                    "copy_files": ("sentencepiece.bpe.model",)},
}

# Opus-MT: one model per pair. Converted CTranslate2 copies that exist on the hub (checked 2026-11-11).
OPUS_CONVERTED = {
    "fr-es": "gaudi/opus-mt-fr-es-ctranslate2",
    "de-es": "gaudi/opus-mt-de-es-ctranslate2",
    "ca-es": "gaudi/opus-mt-ca-es-ctranslate2",
    "ar-es": "gaudi/opus-mt-ar-es-ctranslate2",
    "en-es": "michaelfeil/ct2fast-opus-mt-en-es",      # gaudi's en-es repository has no model.bin
    "ROMANCE-en": "michaelfeil/ct2fast-opus-mt-ROMANCE-en",
    "mul-en": "gaudi/opus-mt-mul-en-ctranslate2",
}
for _lang in ("it", "tr", "ja", "ko", "ru", "uk", "id", "zh", "hi", "ur", "bn"):
    OPUS_CONVERTED["%s-en" % _lang] = "gaudi/opus-mt-%s-en-ctranslate2" % _lang

# Direct Helsinki-NLP models into Spanish that exist only unconverted: used with --convert.
OPUS_DIRECT_UNCONVERTED = ("it", "tr", "ja", "ko", "ru", "uk", "id")

OPUS_ROUTES = {"fr": ["fr-es"], "de": ["de-es"], "ca": ["ca-es"], "ar": ["ar-es"],
               "pt": ["ROMANCE-en", "en-es"],            # no opus-mt-pt-en or pt-es
               "fa": ["mul-en", "en-es"]}                # no opus-mt-fa-en or fa-es
for _lang in ("it", "tr", "ja", "ko", "ru", "uk", "id", "zh", "hi", "ur", "bn"):
    OPUS_ROUTES[_lang] = ["%s-en" % _lang, "en-es"]

_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=SSL_CTX))


class DownloadError(Exception):
    pass


def describe(exc, url):
    """One line a person can act on, never a stack trace."""
    host = urllib.parse.urlsplit(url).hostname or url
    reason = getattr(exc, "reason", None)
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %d from %s" % (exc.code, url)
    if isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError):
        return ("TLS error talking to %s (%s). This network may intercept TLS to some hosts; try another "
                "network, or download the files by hand from %s into the model folder."
                % (host, reason or exc, HUB))
    if isinstance(exc, urllib.error.URLError):
        return "cannot reach %s: %s" % (host, reason)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timed out talking to %s" % host
    return "%s: %s (%s)" % (type(exc).__name__, exc, url)


def _open(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "NewsCentral/1.0 (model download)"})
    return _OPENER.open(req, timeout=timeout)


def repo_info(repo, base=HUB):
    """(revision sha, file names) of a hub repository."""
    url = "%s/api/models/%s" % (base, repo)
    try:
        with _open(url) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["sha"], [s["rfilename"] for s in data.get("siblings", [])]
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError, ValueError, KeyError) as e:
        raise DownloadError(describe(e, url))


def download_file(url, dest, log=print):
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with _open(url) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, shown = 0, 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total > 50 * (1 << 20) and done - shown >= total // 10:
                    shown = done
                    log("  %s: %d of %d MB" % (os.path.basename(dest), done >> 20, total >> 20))
        os.replace(tmp, dest)
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as e:
        raise DownloadError(describe(e, url))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def download_repo(repo, dest, base=HUB, log=print):
    sha, files = repo_info(repo, base)
    log("%s @ %s" % (repo, sha[:10]))
    for name in files:
        if name in SKIP_FILES:
            continue
        parts = name.split("/")
        if name.startswith("/") or "\\" in name or ".." in parts or os.path.isabs(name):
            log("  skipping unsafe file name", repr(name))
            continue
        target = os.path.join(dest, *parts)
        if os.path.isfile(target):          # written via .part + rename, so complete
            continue
        log("  downloading", name)
        download_file("%s/%s/resolve/%s/%s" % (base, repo, sha, urllib.parse.quote(name)), target, log)
    write_json(os.path.join(dest, "source.json"), {"repo": repo, "revision": sha, "method": "download"})


def convert_model(model, dest, copy_files=(), revision="", run=subprocess.run, which=shutil.which):
    """Convert a Transformers model with CTranslate2's converter (needs transformers and torch)."""
    exe = which("ct2-transformers-converter")
    if not exe:
        raise DownloadError("ct2-transformers-converter not found. The --convert path needs CTranslate2's "
                            "converter and its dependencies: py -m pip install -r requirements-translate.txt "
                            "transformers torch")
    cmd = [exe, "--model", model, "--output_dir", dest, "--quantization", "int8", "--force"]
    if copy_files:
        cmd += ["--copy_files"] + list(copy_files)
    proc = run(cmd)
    if proc.returncode != 0:
        raise DownloadError("conversion of %s failed with exit code %d" % (model, proc.returncode))
    write_json(os.path.join(dest, "source.json"), {"repo": model, "revision": revision or "unknown",
                                                   "method": "convert"})


def opus_plan(convert):
    """(routes, downloads {pair: repo}, conversions {pair: model}) for the Opus-MT engine."""
    routes = {lang: list(pairs) for lang, pairs in OPUS_ROUTES.items()}
    if convert:
        for lang in OPUS_DIRECT_UNCONVERTED:
            routes[lang] = ["%s-es" % lang]
    needed = sorted({pair for pairs in routes.values() for pair in pairs})
    downloads = {p: OPUS_CONVERTED[p] for p in needed if p in OPUS_CONVERTED}
    conversions = {p: "Helsinki-NLP/opus-mt-%s" % p for p in needed if p not in OPUS_CONVERTED}
    return routes, downloads, conversions


def get(engine, models_dir, convert=False, base=HUB, log=print, run=subprocess.run):
    """Download (or convert) one engine's model into <models_dir>/<engine>. Returns that folder."""
    dest = os.path.join(models_dir, engine)
    if engine in SINGLE:
        spec = SINGLE[engine]
        if convert:
            sha, _ = repo_info(spec["convert_from"], base)
            convert_model(spec["convert_from"], dest, spec["copy_files"], sha, run)
        else:
            download_repo(spec["repo"], dest, base, log)
        return dest
    if engine == "opus-mt":
        routes, downloads, conversions = opus_plan(convert)
        for pair in sorted(downloads):
            download_repo(downloads[pair], os.path.join(dest, pair), base, log)
        for pair in sorted(conversions):
            sha, _ = repo_info(conversions[pair], base)
            convert_model(conversions[pair], os.path.join(dest, pair), ("source.spm", "target.spm"), sha, run)
        write_json(os.path.join(dest, "routes.json"), routes)
        write_json(os.path.join(dest, "source.json"), {"repo": "several, see routes.json",
                                                       "revision": time.strftime("%Y%m%d"),
                                                       "method": "convert" if convert else "download"})
        return dest
    raise DownloadError("unknown engine %r (known: %s)" % (engine, ", ".join(ENGINE_NAMES)))
