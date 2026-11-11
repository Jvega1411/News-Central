# Translation (spec 2a) Implementation Plan, part 3 of 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Tasks 11 to 16: model downloads, the real engines, the benchmark, the review page, documentation and the benchmark hand-over. Read the header, ground rules, deviations and file structure in `2026-11-11-translation.md` first. Tasks 1 to 10 must be merged before these start.

Facts checked on 2026-11-11 against the Hugging Face hub API (`https://huggingface.co/api/models/<repo>`) and PyPI:

| Engine | Repository downloaded by default | Files | Licence |
|---|---|---|---|
| `nllb-600m` | `JustFrederik/nllb-200-distilled-600M-ct2-int8` (converted with `--quantization int8`, `model.bin` 623 MB) | `model.bin`, `config.json`, `shared_vocabulary.txt`, `sentencepiece.bpe.model`, tokenizer files | CC-BY-NC 4.0 |
| `m2m100-418m` | `michaelfeil/ct2fast-m2m100_418M` (converted with `--quantization float16`, `model.bin` 970 MB) | `model.bin`, `config.json`, `shared_vocabulary.txt`, `sentencepiece.bpe.model`, `vocab.json` | MIT |
| `opus-mt` | `gaudi/opus-mt-{fr,de,ca,ar}-es-ctranslate2`; `gaudi/opus-mt-{it,tr,ja,ko,ru,uk,id,zh,hi,ur,bn}-en-ctranslate2`; `gaudi/opus-mt-mul-en-ctranslate2`; `michaelfeil/ct2fast-opus-mt-ROMANCE-en`; `michaelfeil/ct2fast-opus-mt-en-es` | `model.bin`, `config.json`, `shared_vocabulary.json` or `.txt`, `source.spm`, `target.spm` | Apache-2.0 on the conversions; the Helsinki-NLP models are CC-BY 4.0 |

Not available: `gaudi/opus-mt-en-es-ctranslate2` exists but has no `model.bin`; there is no `Helsinki-NLP/opus-mt-{pt,fa,zh,hi,ur,bn}-es`, no `opus-mt-pt-en` and no `opus-mt-fa-en`. `Helsinki-NLP/opus-mt-{it,tr,ja,ko,ru,uk,id}-es` exist unconverted. The converter path (`--convert`) uses `ct2-transformers-converter` from CTranslate2, which needs `transformers` and `torch` at conversion time only.

Token conventions follow the CTranslate2 "Transformers" guide (opennmt.net/CTranslate2/guides/transformers.html): NLLB source = `[src_code] + pieces + ["</s>"]`, `target_prefix=[[tgt_code]]`, drop the first output token; M2M100 source = `["__src__"] + pieces + ["</s>"]`, `target_prefix=[["__es__"]]`, drop the first output token; Marian source = `pieces + ["</s>"]`, no prefix. **To verify** in the self-checks: that sentencepiece pieces built this way give the same quality as the `transformers` tokenizers the guide uses (the guide tokenizes with `transformers`; this plan avoids that dependency at run time).

---

### Task 11: Model downloads, `tools/get_models.py`

**Files:**
- Create: `newscentral/translate/download.py`, `tools/get_models.py`, `tests/test_get_models.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_get_models.py
import json
import os
import shutil
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from newscentral.translate import download
from newscentral.translate.download import DownloadError

SPEC_LANGS = ["fr", "pt", "it", "de", "ca", "ar", "fa", "tr", "zh", "ja", "ko", "ru", "uk", "hi", "ur", "bn", "id"]
FILES = {".gitattributes": b"*", "README.md": b"# card", "config.json": b"{}", "model.bin": b"x" * 3000,
         "sub/vocab.txt": b"a\nb\n"}


class FakeHub(BaseHTTPRequestHandler):
    """Answers like the Hugging Face hub for any repository: every repo has FILES at revision abc123."""

    requests = []

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        FakeHub.requests.append(self.path)
        if self.path.startswith("/api/models/missing/"):
            return self.reply(404, b"{}")
        if self.path.startswith("/api/models/"):
            body = {"sha": "abc123", "siblings": [{"rfilename": n} for n in FILES]}
            return self.reply(200, json.dumps(body).encode("utf-8"))
        marker = "/resolve/abc123/"
        if marker in self.path:
            name = self.path.split(marker, 1)[1]
            if name in FILES:
                return self.reply(200, FILES[name])
        self.reply(404, b"not found")

    def reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HubCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        FakeHub.requests = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)


def quiet(*parts):
    pass


class TestDownloadRepo(HubCase):
    def test_downloads_every_file_but_the_card(self):
        dest = os.path.join(self.dir, "m")
        download.download_repo("org/model", dest, self.base, quiet)
        self.assertEqual(sorted(os.listdir(dest)), ["config.json", "model.bin", "source.json", "sub"])
        with open(os.path.join(dest, "model.bin"), "rb") as f:
            self.assertEqual(len(f.read()), 3000)
        with open(os.path.join(dest, "source.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"repo": "org/model", "revision": "abc123", "method": "download"})
        self.assertFalse([n for n in os.listdir(dest) if n.endswith(".part")])

    def test_existing_files_are_not_downloaded_again(self):
        dest = os.path.join(self.dir, "m")
        download.download_repo("org/model", dest, self.base, quiet)
        FakeHub.requests = []
        download.download_repo("org/model", dest, self.base, quiet)
        self.assertEqual(FakeHub.requests, ["/api/models/org/model"])

    def test_missing_repository(self):
        with self.assertRaises(DownloadError) as cm:
            download.download_repo("missing/model", os.path.join(self.dir, "m"), self.base, quiet)
        self.assertIn("HTTP 404", str(cm.exception))

    def test_unreachable_host(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                   # nothing listens on this port now
        with self.assertRaises(DownloadError) as cm:
            download.download_repo("org/model", os.path.join(self.dir, "m"), "http://127.0.0.1:%d" % port, quiet)
        self.assertIn("cannot reach 127.0.0.1", str(cm.exception))


class TestMessages(unittest.TestCase):
    def test_tls_interception_has_a_clear_message(self):
        err = urllib.error.URLError(ssl.SSLError(1, "CERTIFICATE_VERIFY_FAILED"))
        msg = download.describe(err, "https://huggingface.co/api/models/x")
        self.assertIn("TLS error talking to huggingface.co", msg)
        self.assertIn("another network", msg)

    def test_http_error(self):
        err = urllib.error.HTTPError("https://huggingface.co/x", 503, "busy", None, None)
        self.assertEqual(download.describe(err, "https://huggingface.co/x"), "HTTP 503 from https://huggingface.co/x")


class TestOpusPlan(unittest.TestCase):
    def test_default_downloads_converted_models_only(self):
        routes, downloads, conversions = download.opus_plan(False)
        self.assertEqual(routes["fr"], ["fr-es"])
        self.assertEqual(routes["it"], ["it-en", "en-es"])
        self.assertEqual(routes["zh"], ["zh-en", "en-es"])
        self.assertEqual(routes["pt"], ["ROMANCE-en", "en-es"])
        self.assertEqual(routes["fa"], ["mul-en", "en-es"])
        self.assertEqual(conversions, {})
        self.assertEqual(downloads["en-es"], "michaelfeil/ct2fast-opus-mt-en-es")
        self.assertEqual(downloads["ar-es"], "gaudi/opus-mt-ar-es-ctranslate2")
        for lang in SPEC_LANGS:
            self.assertIn(lang, routes)
            for pair in routes[lang]:
                self.assertIn(pair, downloads)

    def test_convert_uses_the_direct_models(self):
        routes, downloads, conversions = download.opus_plan(True)
        self.assertEqual(routes["it"], ["it-es"])
        self.assertEqual(conversions["it-es"], "Helsinki-NLP/opus-mt-it-es")
        self.assertNotIn("it-en", downloads)
        self.assertEqual(routes["zh"], ["zh-en", "en-es"])


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_converter_missing(self):
        with self.assertRaises(DownloadError) as cm:
            download.convert_model("facebook/m2m100_418M", self.dir, which=lambda name: None)
        self.assertIn("ct2-transformers-converter not found", str(cm.exception))

    def test_command(self):
        calls = []

        class Done:
            returncode = 0

        def run(cmd):
            calls.append(cmd)
            return Done()

        dest = os.path.join(self.dir, "it-es")
        download.convert_model("Helsinki-NLP/opus-mt-it-es", dest, ("source.spm", "target.spm"), "def456",
                               run=run, which=lambda name: "/bin/" + name)
        self.assertEqual(calls, [["/bin/ct2-transformers-converter", "--model", "Helsinki-NLP/opus-mt-it-es",
                                  "--output_dir", dest, "--quantization", "int8", "--force",
                                  "--copy_files", "source.spm", "target.spm"]])
        with open(os.path.join(dest, "source.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["revision"], "def456")

    def test_failed_conversion(self):
        class Failed:
            returncode = 2
        with self.assertRaises(DownloadError):
            download.convert_model("x/y", self.dir, run=lambda cmd: Failed(), which=lambda name: "/bin/" + name)


class TestGet(HubCase):
    def test_single_model_engine(self):
        dest = download.get("nllb-600m", self.dir, base=self.base, log=quiet)
        self.assertEqual(dest, os.path.join(self.dir, "nllb-600m"))
        self.assertIn("/api/models/JustFrederik/nllb-200-distilled-600M-ct2-int8", FakeHub.requests)

    def test_opus_mt_writes_the_routes(self):
        dest = download.get("opus-mt", self.dir, base=self.base, log=quiet)
        with open(os.path.join(dest, "routes.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), download.opus_plan(False)[0])
        self.assertTrue(os.path.isfile(os.path.join(dest, "zh-en", "model.bin")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "source.json")))

    def test_unknown_engine(self):
        with self.assertRaises(DownloadError):
            download.get("nope", self.dir, base=self.base, log=quiet)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_get_models -v`
Expected: `ImportError: cannot import name 'download' from 'newscentral.translate'`.

- [ ] **Step 3: The download module**

```python
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
        target = os.path.join(dest, *name.split("/"))
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
```

- [ ] **Step 4: The tool**

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest tests.test_get_models -v`, then `py -m unittest discover -s tests`.
Expected: all pass. No test touches the network beyond `127.0.0.1`.

- [ ] **Step 6: Try a real download by hand**

Run: `py tools/get_models.py nllb-600m`. Expected: `JustFrederik/nllb-200-distilled-600M-ct2-int8 @ <sha>`, one `downloading` line per file, progress lines for `model.bin`, then `Model ready in ...\data\models\nllb-600m`. If the network intercepts TLS, expected: one line `Could not get nllb-600m: TLS error talking to ...` and exit code 1, no traceback; retry from another network. Confirm `git status --short` shows nothing under `data/`.

- [ ] **Step 7: Commit**

```bash
git add newscentral/translate/download.py tools/get_models.py tests/test_get_models.py
git commit -m "Add tools/get_models.py to download or convert translation models" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Engines on CTranslate2, `requirements-translate.txt`, self-check

**Files:**
- Create: `newscentral/translate/ct2.py`, `newscentral/translate/nllb.py`, `newscentral/translate/m2m100.py`, `newscentral/translate/opus_mt.py`, `newscentral/translate/selfcheck.py`, `requirements-translate.txt`, `tests/test_engines.py`
- Modify: `newscentral/translate/registry.py`

- [ ] **Step 1: Write the failing tests**

The tests never load CTranslate2 or sentencepiece. They check the availability rules and the token glue with stand-in objects injected where the real ones would be loaded.

```python
# tests/test_engines.py
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from newscentral.translate import base, m2m100, nllb, opus_mt, registry

SPEC_LANGS = ["fr", "pt", "it", "de", "ca", "ar", "fa", "tr", "zh", "ja", "ko", "ru", "uk", "hi", "ur", "bn", "id"]


class FakePieces:
    """Stands in for a sentencepiece processor: pieces are words."""

    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, pieces):
        return " ".join(pieces)


class Result:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class FakeTranslator:
    """Stands in for ctranslate2.Translator: upper-cases the word tokens, keeps the target prefix."""

    def __init__(self):
        self.calls = []

    def translate_batch(self, source, target_prefix=None, beam_size=2, max_decoding_length=256):
        self.calls.append({"source": source, "target_prefix": target_prefix})
        out = []
        for i, tokens in enumerate(source):
            words = [t.upper() for t in tokens if t != "</s>" and "_" not in t]
            prefix = target_prefix[i] if target_prefix else []
            out.append(Result(list(prefix) + words + ["</s>"]))
        return out


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def touch(self, *parts):
        path = os.path.join(self.dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x")


class TestAvailability(EngineCase):
    def test_registry_names(self):
        self.assertEqual(sorted(registry.ENGINES), ["fake", "m2m100-418m", "nllb-600m", "opus-mt"])

    def test_missing_packages(self):
        with mock.patch("importlib.util.find_spec", return_value=None):
            reason = registry.check("nllb-600m", self.dir)
        self.assertIn("ctranslate2, sentencepiece", reason)
        self.assertIn("requirements-translate.txt", reason)

    def test_missing_models(self):
        with mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertIn("py tools/get_models.py nllb-600m", registry.check("nllb-600m", self.dir))
            self.assertIn("py tools/get_models.py m2m100-418m", registry.check("m2m100-418m", self.dir))
            self.assertIn("py tools/get_models.py opus-mt", registry.check("opus-mt", self.dir))
            self.touch("nllb-600m", "model.bin")
            self.assertEqual(registry.check("nllb-600m", self.dir), "")
            self.assertEqual(registry.installed(self.dir), ["nllb-600m"])

    def test_creating_an_engine_loads_nothing(self):
        engine = registry.create("nllb-600m", self.dir, threads=3)
        self.assertIsNone(engine._translator)
        self.assertEqual(engine.threads, 3)
        self.assertEqual(engine.translate([], "ar", "es"), [])

    def test_version_from_source_json(self):
        os.makedirs(os.path.join(self.dir, "nllb-600m"))
        with open(os.path.join(self.dir, "nllb-600m", "source.json"), "w", encoding="utf-8") as f:
            json.dump({"repo": "r", "revision": "0123456789abcdef"}, f)
        self.assertEqual(registry.create("nllb-600m", self.dir).version, "0123456789ab")
        self.assertEqual(registry.create("m2m100-418m", self.dir).version, "unknown")


class TestLanguages(unittest.TestCase):
    def test_spec_languages_are_covered(self):
        n = nllb.NllbEngine("unused")
        m = m2m100.M2M100Engine("unused")
        for lang in SPEC_LANGS:
            self.assertTrue(n.supports(lang, "es"), lang)
            self.assertTrue(m.supports(lang, "es"), lang)
        self.assertFalse(n.supports("es", "es"))
        self.assertFalse(m.supports("eu", "es"))       # M2M100 has no Basque
        self.assertTrue(n.supports("eu", "es"))

    def test_codes(self):
        self.assertEqual([nllb.NLLB_CODES[c] for c in ("ar", "fa", "zh", "es", "ms")],
                         ["arb_Arab", "pes_Arab", "zho_Hans", "spa_Latn", "zsm_Latn"])
        self.assertEqual(m2m100.lang_token("es"), "__es__")


class TestTokenGlue(EngineCase):
    def test_strip_special(self):
        self.assertEqual(base.strip_special(["<s>", "a", "</s>", "<pad>"]), ["a"])

    def test_nllb(self):
        engine = nllb.NllbEngine(self.dir)
        engine._sp, engine._translator = FakePieces(), FakeTranslator()
        self.assertEqual(engine.translate(["hola mundo", "otra"], "fr", "es"), ["HOLA MUNDO", "OTRA"])
        call = engine._translator.calls[0]
        self.assertEqual(call["source"][0], ["fra_Latn", "hola", "mundo", "</s>"])
        self.assertEqual(call["target_prefix"], [["spa_Latn"], ["spa_Latn"]])

    def test_m2m100(self):
        engine = m2m100.M2M100Engine(self.dir)
        engine._sp, engine._translator = FakePieces(), FakeTranslator()
        self.assertEqual(engine.translate(["bonjour"], "fr", "es"), ["BONJOUR"])
        call = engine._translator.calls[0]
        self.assertEqual(call["source"], [["__fr__", "bonjour", "</s>"]])
        self.assertEqual(call["target_prefix"], [["__es__"]])

    def test_opus_routes_and_pivot(self):
        os.makedirs(os.path.join(self.dir, "opus-mt"))
        with open(os.path.join(self.dir, "opus-mt", "routes.json"), "w", encoding="utf-8") as f:
            json.dump({"fr": ["fr-es"], "zh": ["zh-en", "en-es"]}, f)
        self.touch("opus-mt", "fr-es", "model.bin")
        self.touch("opus-mt", "zh-en", "model.bin")
        engine = opus_mt.OpusMtEngine(os.path.join(self.dir, "opus-mt"))
        self.assertTrue(engine.supports("fr", "es"))
        self.assertFalse(engine.supports("zh", "es"))            # en-es missing
        self.assertFalse(engine.supports("fr", "en"))
        self.touch("opus-mt", "en-es", "model.bin")
        self.assertTrue(engine.supports("zh", "es"))
        translators = {}
        for pair in ("fr-es", "zh-en", "en-es"):
            translators[pair] = FakeTranslator()
            engine._pairs[pair] = (translators[pair], FakePieces(), FakePieces())
        self.assertEqual(engine.translate(["ni hao"], "zh", "es"), ["NI HAO"])
        self.assertEqual(translators["zh-en"].calls[0]["source"], [["ni", "hao", "</s>"]])
        self.assertEqual(translators["en-es"].calls[0]["source"], [["NI", "HAO", "</s>"]])
        self.assertIsNone(translators["zh-en"].calls[0]["target_prefix"])
        self.assertEqual(translators["fr-es"].calls, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_engines -v`
Expected: `ImportError: cannot import name 'm2m100' from 'newscentral.translate'`.

- [ ] **Step 3: Shared loaders**

```python
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
```

- [ ] **Step 4: NLLB**

```python
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
```

Chinese sources in Traditional script would want `zho_Hant`; the language reference has no script variant, so `zh` maps to Simplified. Serbian sources in Latin script get `srp_Cyrl`. Both are noted for the benchmark.

- [ ] **Step 5: M2M100**

```python
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
```

- [ ] **Step 6: Opus-MT**

```python
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
```

- [ ] **Step 7: Register the engines**

Replace `newscentral/translate/registry.py` from the imports through the `ENGINES` dictionary with:

```python
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
```

The functions `check`, `create` and `installed` stay as they are.

- [ ] **Step 8: Self-check module and requirements file**

```python
# newscentral/translate/selfcheck.py
# Translate three fixed sentences with one real engine and print the result and the time.
# Usage: py -m newscentral.translate.selfcheck nllb-600m | m2m100-418m | opus-mt [--config config.json]
import argparse
import sys
import time

from ..config import CONFIG_FILE, TARGET_LANG, load_config, paths, worker_threads
from ..text import canonical
from . import registry

SENTENCES = (
    ("fr", "La Banque centrale europ\u00e9enne a relev\u00e9 ses taux d'int\u00e9r\u00eat."),
    ("ar", "\u0627\u0631\u062a\u0641\u0639 \u0645\u0639\u062f\u0644 \u0627\u0644\u062a\u0636\u062e\u0645 "
           "\u0641\u064a \u0645\u0635\u0631"),
    ("zh", "\u4e2d\u56fd\u653f\u5e9c\u5ba3\u5e03\u65b0\u7684\u7ecf\u6d4e\u523a\u6fc0\u8ba1\u5212"),
)


def selfcheck(name, models_dir, threads, out=print):
    reason = registry.check(name, models_dir)
    if reason:
        out("%s is unavailable: %s" % (name, reason))
        return 1
    engine = registry.create(name, models_dir, threads)
    out("%s, model version %s, %d thread(s)" % (name, engine.version, threads))
    bad = 0
    for src, text in SENTENCES:
        if not engine.supports(src, TARGET_LANG):
            out("%s: not supported by this engine" % src)
            continue
        start = time.perf_counter()
        result = engine.translate([text], src, TARGET_LANG)[0]
        out("%s -> %s (%.2f s): %s" % (src, TARGET_LANG, time.perf_counter() - start, result))
        if not canonical(result):
            bad += 1
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Translate three sentences with one engine")
    ap.add_argument("engine", choices=sorted(n for n in registry.ENGINES if n != "fake"))
    ap.add_argument("--config", default=CONFIG_FILE)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config(args.config)
    return selfcheck(args.engine, paths(cfg)["models"], worker_threads(cfg))


if __name__ == "__main__":
    sys.exit(main())
```

```
# requirements-translate.txt
# Optional: local translation engines (spec 2a). Needs Python 3.10 or newer.
#   py -m pip install -r requirements-translate.txt
# Without these packages the translate job reports "unavailable" and the reader shows originals.
# Wheels: win_amd64, macOS x86_64/arm64 and manylinux aarch64 (Raspberry Pi OS 64-bit) on PyPI.
ctranslate2>=4.5,<5
sentencepiece>=0.2.0
# Only for "py tools/get_models.py <engine> --convert" (large, not needed for the default downloads):
#   py -m pip install transformers torch
```

Write that file without the first comment line `# requirements-translate.txt`.

- [ ] **Step 9: Run the tests**

Run: `py -m unittest tests.test_engines -v`, then `py -m unittest discover -s tests`.
Expected: all pass, with or without `ctranslate2` installed.

- [ ] **Step 10: Self-check every real engine by hand**

```bash
py -m pip install -r requirements-translate.txt
py tools/get_models.py nllb-600m
py tools/get_models.py m2m100-418m
py tools/get_models.py opus-mt
py -m newscentral.translate.selfcheck nllb-600m
py -m newscentral.translate.selfcheck m2m100-418m
py -m newscentral.translate.selfcheck opus-mt
```

Expected for each self-check: a header line with the version (the first 12 characters of the hub revision, or a date for `opus-mt`), then three lines, `fr -> es`, `ar -> es`, `zh -> es`, each with a time and a Spanish sentence: roughly "El Banco Central Europeo subió sus tipos de interés", "La tasa de inflación aumentó en Egipto" and "El Gobierno chino anuncia un nuevo plan de estímulo económico". The first line of each run is slower (model load, a few seconds); the others should take under a second on this laptop. Exit code 0. For `opus-mt` the `zh` line goes through English and may read less naturally. Output that is empty, repeats one word, contains `__es__`, `spa_Latn` or `</s>`, or stays in the source language means the token conventions are wrong for that engine: stop and report the output instead of committing.

Then wire one engine end to end: in the real `config.json` set `"translate_engine": {"default": "nllb-600m"}` (the default), start `py server.py --port 8799`, wait five minutes and open Sources: `Worker job translate: running` or `idle`, a non-zero `done in the last hour`, and in the feed real Spanish titles for French and Portuguese items. Stop the server. Nothing under `data/` is staged.

- [ ] **Step 11: Commit**

```bash
git add newscentral/translate/ct2.py newscentral/translate/nllb.py newscentral/translate/m2m100.py newscentral/translate/opus_mt.py newscentral/translate/selfcheck.py newscentral/translate/registry.py requirements-translate.txt tests/test_engines.py
git commit -m "Add NLLB, M2M100 and Opus-MT engines on CTranslate2" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Benchmark tool

**Files:**
- Create: `newscentral/translate/bench.py`, `tools/bench_translate.py`, `tests/test_bench.py`
- Modify: `newscentral/store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bench.py
import contextlib
import io
import json
import os
import unittest

from newscentral.translate import bench
from newscentral.translate.fake import FakeEngine
from tests.helpers import TranslationCase, load_tool


class ArabicOnly(FakeEngine):
    def supports(self, src, tgt):
        return src == "ar"


def ticking(step=0.5):
    state = [0.0]

    def clock():
        state[0] += step
        return state[0]
    return clock


class TestSample(TranslationCase):
    def test_newest_per_language_with_both_fields(self):
        units = bench.sample_units(self.store, self.READ, per_lang=1)
        self.assertEqual([u["key"] for u in units], [
            self.ids["old_ar"] + "|title", self.ids["old_ar"] + "|summary",
            self.ids["new_ru"] + "|title", self.ids["new_ru"] + "|summary"])
        self.assertEqual(units[0]["lang"], "ar")
        self.assertEqual(units[0]["source_id"], "ahram")

    def test_items_without_summary_give_one_unit(self):
        keys = [u["key"] for u in bench.sample_units(self.store, self.READ, per_lang=15)]
        self.assertIn(self.ids["ar_no_summary"] + "|title", keys)
        self.assertNotIn(self.ids["ar_no_summary"] + "|summary", keys)
        self.assertEqual(len(keys), 5)


class TestRun(TranslationCase):
    def test_records_text_and_seconds(self):
        units = bench.sample_units(self.store, self.READ, per_lang=1)
        result = bench.run_bench(units, {"fake": FakeEngine("x")}, batch_size=16, clock=ticking(), date="2026-11-20")
        self.assertEqual(result["date"], "2026-11-20")
        self.assertEqual(result["target"], "es")
        tr = result["items"][0]["translations"]["fake"]
        self.assertTrue(tr["text"].startswith("[ar>es] "))
        self.assertEqual((tr["seconds"], tr["error"]), (0.5, ""))        # one unit per batch, 0.5 s per tick
        self.assertEqual(result["engines"]["fake"]["errors"], 0)
        self.assertEqual(result["engines"]["fake"]["seconds_per_item"], {"title": 0.5, "summary": 0.5})

    def test_unsupported_languages_are_marked(self):
        units = bench.sample_units(self.store, self.READ, per_lang=1)
        result = bench.run_bench(units, {"ar-only": ArabicOnly("x")}, clock=ticking())
        by_key = {i["key"]: i["translations"]["ar-only"] for i in result["items"]}
        self.assertEqual(by_key[self.ids["new_ru"] + "|title"]["error"], "unsupported")
        self.assertEqual(by_key[self.ids["old_ar"] + "|title"]["error"], "")


class TestSummary(unittest.TestCase):
    def bench(self):
        def item(key, lang, a, b):
            return {"key": key, "lang": lang, "translations": {"a": a, "b": b}}
        ok_a = {"text": "t", "seconds": 0.5, "error": ""}
        ok_b = {"text": "t", "seconds": 0.1, "error": ""}
        return {"engines": {"a": {}, "b": {}}, "items": [
            item("1|title", "ar", ok_a, ok_b),
            item("2|title", "zh", ok_a, ok_b),
            item("3|title", "zh", ok_a, {"text": "", "seconds": 0.1, "error": "empty output"}),
            item("4|title", "ko", ok_a, {"text": "", "seconds": None, "error": "unsupported"}),
        ]}

    def test_adequacy_first_then_speed(self):
        scores = {"1|title": {"a": {"adequacy": 4, "fluency": 4}, "b": {"adequacy": 4.04, "fluency": 3}},
                  "2|title": {"a": {"adequacy": 3, "fluency": 3}, "b": {"adequacy": 3, "fluency": 3}},
                  "3|title": {"a": {"adequacy": 3, "fluency": 3}},
                  "4|title": {"a": {"adequacy": 2, "fluency": 2}}}
        out = bench.summarize(self.bench(), scores)
        self.assertEqual(out["choice"], {"ar": "b", "ko": "a", "zh": "a"})
        self.assertEqual(out["by_lang"]["zh"]["b"]["adequacy"], 2.0)   # the error counts as 1
        self.assertNotIn("b", out["by_lang"]["ko"])                    # unsupported is not a candidate
        self.assertEqual(out["translate_engine"], {"default": "a", "ar": "b"})

    def test_compress(self):
        self.assertEqual(bench.compress({}), {})
        self.assertEqual(bench.compress({"ar": "x", "fr": "y", "zh": "y"}), {"default": "y", "ar": "x"})


class TestFiles(TranslationCase):
    def test_paths_reject_bad_dates(self):
        self.assertTrue(bench.bench_path(self.dir, "2026-11-20", "scores").endswith("translate-2026-11-20-scores.json"))
        with self.assertRaises(ValueError):
            bench.bench_path(self.dir, "../../etc")

    def test_round_trip_and_review(self):
        bench_dir = os.path.join(self.dir, "bench")
        data = {"date": "2026-11-20", "engines": {"fake": {}}, "items": [{"key": "k%d" % n} for n in range(40)]}
        bench.write_json(bench.bench_path(bench_dir, "2026-11-20"), data)
        bench.write_json(bench.bench_path(bench_dir, "2026-11-20", "scores"),
                         {"scores": {"k%d" % n: {"fake": {"adequacy": 4, "fluency": 4}} for n in range(30)}})
        self.assertEqual(bench.list_dates(bench_dir), ["2026-11-20"])
        loaded, scores, review = bench.load_round(bench_dir, "2026-11-20")
        self.assertEqual((len(loaded["items"]), len(scores), review), (40, 30, {}))
        sample = bench.review_sample(loaded, scores)
        self.assertEqual(len(sample), 25)
        self.assertEqual(sample, bench.review_sample(loaded, scores))     # stable
        self.assertTrue(set(sample) <= set(scores))
        bench.save_review(bench_dir, "2026-11-20", "k1", "fake", True, "meaning lost")
        self.assertEqual(bench.load_round(bench_dir, "2026-11-20")[2],
                         {"k1": {"fake": {"disagree": True, "note": "meaning lost"}}})


class TestTool(TranslationCase):
    def test_run_with_the_fake_engine(self):
        config = os.path.join(self.dir, "config.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": self.dir}, f)
        tool = load_tool("bench_translate")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tool.main(["run", "--config", config, "--engines", "fake", "--per-lang", "2"])
        self.assertEqual(code, 0, out.getvalue())
        dates = bench.list_dates(os.path.join(self.dir, "bench"))
        self.assertEqual(len(dates), 1)
        data, _, _ = bench.load_round(os.path.join(self.dir, "bench"), dates[0])
        self.assertEqual(len(data["items"]), 5)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(["summarize", "--config", config]), 1)    # no scores yet


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_bench -v`
Expected: `ImportError: cannot import name 'bench' from 'newscentral.translate'`.

- [ ] **Step 3: Sampling in the store**

Append to `class Store` in `newscentral/store.py`:

```python
    def sample_by_lang(self, skip_langs, per_lang):
        """The newest `per_lang` items of every language not in skip_langs, languages in
        alphabetical order. For the translation benchmark."""
        skip = list(skip_langs)
        marks = ",".join("?" * len(skip))
        with self.lock:
            langs = [r[0] for r in self.db.execute(
                "SELECT DISTINCT lang FROM items WHERE lang IS NOT NULL AND lang NOT IN (%s) ORDER BY lang"
                % marks, skip)]
            rows = []
            for lang in langs:
                rows.extend(dict(r) for r in self.db.execute(
                    "SELECT id, source_id, lang, title, summary, first_seen FROM items WHERE lang = ? "
                    "ORDER BY first_seen DESC LIMIT ?", (lang, per_lang)))
        return rows
```

- [ ] **Step 4: The benchmark module**

```python
# newscentral/translate/bench.py
# Translation benchmark: sample archived items, run every engine, record text and seconds per
# item, then summarize Claude's scores into one engine per source language.
# Files in <data_dir>/bench/: translate-<date>.json (this module), translate-<date>-scores.json
# (written by Claude in a Claude Code session), translate-<date>-review.json (the review page).
import hashlib
import json
import os
import platform
import re
import time

from ..config import TARGET_LANG
from ..text import canonical
from .job import check_output

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_RE = re.compile(r"^translate-(\d{4}-\d{2}-\d{2})\.json$")
REVIEW_SAMPLE = 25


def bench_path(bench_dir, date, kind=""):
    if not DATE_RE.match(date or ""):
        raise ValueError("date must be YYYY-MM-DD")
    return os.path.join(bench_dir, "translate-%s%s.json" % (date, "-" + kind if kind else ""))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def sample_units(store, read_langs, per_lang=15, target=TARGET_LANG):
    units = []
    for r in store.sample_by_lang(list(read_langs) + [target], per_lang):
        for field in ("title", "summary"):
            text = r[field] or ""
            if text:
                units.append({"key": "%s|%s" % (r["id"], field), "item_id": r["id"], "source_id": r["source_id"],
                              "lang": r["lang"], "field": field, "text": text, "first_seen": r["first_seen"]})
    return units


def run_bench(units, engines, batch_size=16, clock=time.perf_counter, date=None):
    """engines: {name: Engine}. Every engine translates every unit it supports, in batches per
    (language, field); seconds per item = batch time / batch size."""
    items = [dict(u, translations={}) for u in units]
    groups = {}
    for it in items:
        groups.setdefault((it["lang"], it["field"]), []).append(it)
    summary = {}
    for name in sorted(engines):
        engine = engines[name]
        times = {"title": [], "summary": []}
        errors = 0
        for lang, field in sorted(groups):
            group = groups[(lang, field)]
            for start in range(0, len(group), batch_size):
                chunk = group[start:start + batch_size]
                if not engine.supports(lang, TARGET_LANG):
                    for it in chunk:
                        it["translations"][name] = {"text": "", "seconds": None, "error": "unsupported"}
                    continue
                began = clock()
                try:
                    outputs = engine.translate([it["text"] for it in chunk], lang, TARGET_LANG)
                    failure = ""
                except Exception as e:
                    outputs = [""] * len(chunk)
                    failure = ("%s: %s" % (type(e).__name__, e))[:300]
                per = (clock() - began) / len(chunk)
                for it, output in zip(chunk, outputs):
                    text, error = check_output(it["text"], output)
                    error = failure or error
                    if error:
                        errors += 1
                        text = canonical(output or "")
                    else:
                        times[field].append(per)
                    it["translations"][name] = {"text": text, "seconds": round(per, 4), "error": error}
        summary[name] = {"version": engine.version, "errors": errors,
                         "seconds_per_item": {f: (round(sum(v) / len(v), 4) if v else None)
                                              for f, v in times.items()}}
    return {"date": date or time.strftime("%Y-%m-%d"), "machine": platform.platform(),
            "python": platform.python_version(), "target": TARGET_LANG, "engines": summary, "items": items}


def _mean(values):
    return round(sum(values) / len(values), 3) if values else None


def compress(choice):
    """{lang: engine} to a translate_engine config value: the most chosen engine as default."""
    if not choice:
        return {}
    counts = {}
    for engine in choice.values():
        counts[engine] = counts.get(engine, 0) + 1
    default = sorted(counts, key=lambda e: (-counts[e], e))[0]
    out = {"default": default}
    for lang in sorted(choice):
        if choice[lang] != default:
            out[lang] = choice[lang]
    return out


def summarize(data, scores):
    """Mean adequacy, fluency and seconds per engine and source language. An engine that failed on
    an item scores 1 on it; an engine that does not support a language is not a candidate for it.
    Choice: highest mean adequacy (one decimal), then fastest, then name."""
    table = {}
    for it in data["items"]:
        for name, tr in it["translations"].items():
            if tr["error"] == "unsupported":
                continue
            acc = table.setdefault(it["lang"], {}).setdefault(name, {"adequacy": [], "fluency": [], "seconds": []})
            if tr["error"]:
                acc["adequacy"].append(1)
                acc["fluency"].append(1)
                continue
            s = (scores.get(it["key"]) or {}).get(name)
            if s:
                acc["adequacy"].append(s["adequacy"])
                acc["fluency"].append(s["fluency"])
            if tr["seconds"] is not None:
                acc["seconds"].append(tr["seconds"])
    by_lang, choice = {}, {}
    for lang in sorted(table):
        rows = {}
        for name, acc in table[lang].items():
            if acc["adequacy"]:
                rows[name] = {"adequacy": _mean(acc["adequacy"]), "fluency": _mean(acc["fluency"]),
                              "seconds": _mean(acc["seconds"]), "n": len(acc["adequacy"])}
        if rows:
            by_lang[lang] = rows
            choice[lang] = sorted(rows, key=lambda n: (-round(rows[n]["adequacy"], 1),
                                                       rows[n]["seconds"] if rows[n]["seconds"] is not None
                                                       else float("inf"), n))[0]
    return {"by_lang": by_lang, "choice": choice, "translate_engine": compress(choice)}


def list_dates(bench_dir):
    try:
        names = os.listdir(bench_dir)
    except OSError:
        return []
    return sorted(m.group(1) for m in (FILE_RE.match(n) for n in names) if m)


def load_round(bench_dir, date):
    """(benchmark, scores {key: {engine: {...}}}, review {key: {engine: {...}}})."""
    data = read_json(bench_path(bench_dir, date))
    if data is None:
        raise ValueError("no benchmark for %s" % date)
    scores = (read_json(bench_path(bench_dir, date, "scores"), {}) or {}).get("scores", {})
    review = (read_json(bench_path(bench_dir, date, "review"), {}) or {}).get("review", {})
    return data, scores, review


def review_sample(data, scores, n=REVIEW_SAMPLE):
    """A stable pseudo-random sample of scored items for the user to check."""
    keys = [it["key"] for it in data["items"] if it["key"] in scores]
    keys.sort(key=lambda k: hashlib.sha1(k.encode("utf-8")).hexdigest())
    return keys[:n]


def save_review(bench_dir, date, key, engine, disagree, note):
    path = bench_path(bench_dir, date, "review")
    data = read_json(path, None) or {"date": date, "review": {}}
    data.setdefault("review", {}).setdefault(key, {})[engine] = {"disagree": bool(disagree),
                                                                 "note": str(note or "")[:1000]}
    write_json(path, data)
```

- [ ] **Step 5: The tool**

```python
#!/usr/bin/env python3
# Translation benchmark. Reads the archive through the database the server owns (WAL, safe
# while it runs; stop the worker with --no-worker first so it does not compete for the CPU).
# Usage: py tools/bench_translate.py run [--engines nllb-600m,opus-mt] [--per-lang 15]
#        py tools/bench_translate.py summarize [--date YYYY-MM-DD]
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import CONFIG_FILE, ConfigError, load_config, paths, worker_threads   # noqa: E402
from newscentral.store import SchemaMismatch, Store                                          # noqa: E402
from newscentral.translate import bench, registry                                            # noqa: E402


def run(cfg, p, args):
    try:
        store = Store(p["db"], migrate=False)
    except (SchemaMismatch, FileNotFoundError) as e:
        print(e)
        return 1
    try:
        units = bench.sample_units(store, cfg["read_langs"], args.per_lang)
    finally:
        store.db.close()
    names = [n for n in args.engines.split(",") if n] if args.engines else registry.installed(p["models"])
    if not names:
        print("No translation engine is installed. See requirements-translate.txt and tools/get_models.py.")
        return 1
    for name in names:
        reason = registry.check(name, p["models"])
        if reason:
            print("%s: %s" % (name, reason))
            return 1
    print("%d units in %d languages; engines: %s" % (len(units), len({u["lang"] for u in units}), ", ".join(names)))
    engines = {n: registry.create(n, p["models"], worker_threads(cfg)) for n in names}
    data = bench.run_bench(units, engines, cfg["translate_batch"])
    path = bench.bench_path(p["bench"], data["date"])
    bench.write_json(path, data)
    for name, info in sorted(data["engines"].items()):
        print("%-14s version %-12s errors %4d  s/title %s  s/summary %s" % (
            name, info["version"], info["errors"], info["seconds_per_item"]["title"],
            info["seconds_per_item"]["summary"]))
    print("Wrote", path)
    return 0


def summarize(p, args):
    dates = bench.list_dates(p["bench"])
    date = args.date or (dates[-1] if dates else "")
    if date not in dates:
        print("No benchmark found in", p["bench"])
        return 1
    data, scores, review = bench.load_round(p["bench"], date)
    if not scores:
        print("No scores yet: %s is missing" % bench.bench_path(p["bench"], date, "scores"))
        return 1
    out = bench.summarize(data, scores)
    print("%-5s %-14s %9s %8s %9s %5s" % ("LANG", "ENGINE", "ADEQUACY", "FLUENCY", "S/ITEM", "N"))
    for lang, rows in out["by_lang"].items():
        for name, r in sorted(rows.items()):
            mark = " <" if out["choice"].get(lang) == name else ""
            print("%-5s %-14s %9s %8s %9s %5d%s" % (lang, name, r["adequacy"], r["fluency"], r["seconds"], r["n"], mark))
    disagreed = sum(1 for k in review.values() for v in k.values() if v.get("disagree"))
    reviewed = sum(len(k) for k in review.values())
    print("Review: %d of %d checked scores marked as wrong" % (disagreed, reviewed))
    print('Put this in config.json: "translate_engine": %s' % json.dumps(out["translate_engine"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Translation benchmark")
    sub = ap.add_subparsers(dest="command")
    r = sub.add_parser("run")
    r.add_argument("--engines", default="", help="comma-separated engine names (default: every installed one)")
    r.add_argument("--per-lang", type=int, default=15)
    r.add_argument("--config", default=CONFIG_FILE)
    s = sub.add_parser("summarize")
    s.add_argument("--date", default="")
    s.add_argument("--config", default=CONFIG_FILE)
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(e)
        return 2
    p = paths(cfg)
    return run(cfg, p, args) if args.command == "run" else summarize(p, args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests**

Run: `py -m unittest tests.test_bench -v`, then `py -m unittest discover -s tests`.
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add newscentral/store.py newscentral/translate/bench.py tools/bench_translate.py tests/test_bench.py
git commit -m "Add the translation benchmark tool" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The `/bench/translate` review page

**Files:**
- Modify: `newscentral/api.py`
- Create: `static/bench.html`, `static/js/bench.js`, `tests/test_bench_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bench_api.py
import os
import unittest

from newscentral.translate import bench
from tests.helpers import ApiCase

DATE = "2026-11-20"


class TestBenchApi(ApiCase):
    def setUp(self):
        super().setUp()
        self.app.config["data_dir"] = self.dir
        self.bench_dir = os.path.join(self.dir, "bench")
        items = [{"key": "a1|title", "item_id": "a1", "source_id": "ahram", "lang": "ar", "field": "title",
                  "text": "\u062e\u0628\u0631", "translations": {
                      "x": {"text": "Noticia", "seconds": 0.2, "error": ""},
                      "y": {"text": "", "seconds": 0.1, "error": "empty output"}}},
                 {"key": "r1|title", "item_id": "r1", "source_id": "meduza", "lang": "ru", "field": "title",
                  "text": "\u041d\u043e\u0432\u043e\u0441\u0442\u0438", "translations": {
                      "x": {"text": "Noticias", "seconds": 0.2, "error": ""},
                      "y": {"text": "Novedades", "seconds": 0.1, "error": ""}}}]
        bench.write_json(bench.bench_path(self.bench_dir, DATE),
                         {"date": DATE, "machine": "test", "engines": {"x": {}, "y": {}}, "items": items})
        bench.write_json(bench.bench_path(self.bench_dir, DATE, "scores"),
                         {"scores": {"a1|title": {"x": {"adequacy": 5, "fluency": 4, "note": ""}}}})

    def test_payload(self):
        status, body = self.get("/api/bench/translate")
        self.assertEqual(status, 200)
        self.assertEqual((body["dates"], body["date"], body["engines"]), ([DATE], DATE, ["x", "y"]))
        self.assertEqual(body["sample"], ["a1|title"])
        self.assertTrue(body["has_scores"])
        first = body["items"][0]
        self.assertEqual(first["scores"]["x"]["adequacy"], 5)
        self.assertEqual(first["review"], {})

    def test_no_benchmark_yet(self):
        os.remove(bench.bench_path(self.bench_dir, DATE))
        _, body = self.get("/api/bench/translate")
        self.assertEqual((body["dates"], body["date"], body["items"]), ([], None, []))

    def test_unknown_date(self):
        self.assertEqual(self.get("/api/bench/translate?date=2020-01-01")[0], 400)
        self.assertEqual(self.get("/api/bench/translate?date=..%2F..%2Fx")[0], 400)

    def test_review_is_saved(self):
        status, body = self.post("/api/bench/review", {"date": DATE, "key": "a1|title", "engine": "x",
                                                       "disagree": True, "note": "too literal"})
        self.assertEqual((status, body), (200, {"ok": True}))
        _, payload = self.get("/api/bench/translate")
        self.assertEqual(payload["items"][0]["review"], {"x": {"disagree": True, "note": "too literal"}})

    def test_bad_reviews(self):
        for body in ({"date": DATE, "key": "nope", "engine": "x"},
                     {"date": DATE, "key": "a1|title", "engine": "z"},
                     {"date": "../x", "key": "a1|title", "engine": "x"}):
            self.assertEqual(self.post("/api/bench/review", body)[0], 400, body)
        self.assertEqual(self.post("/api/bench/review", {"date": DATE}, header=False)[0], 403)

    def test_page(self):
        status, html = self.get("/bench/translate")
        self.assertEqual(status, 200)
        self.assertIn('src="/js/bench.js"', html)
        self.assertEqual(self.get("/js/bench.js")[0], 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_bench_api -v`
Expected: 404 responses (`AssertionError: 404 != 200`).

- [ ] **Step 3: API**

In `newscentral/api.py`, extend the config import with `paths`:

```python
from .config import DEFAULTS, INDEX_FILE, LIVE_ROW_LIMIT, LIVE_WINDOW_HOURS, STATIC_DIR, TARGET_LANG, paths
```

and add below the other package imports:

```python
from .translate import bench
```

Add above `def static_path(path):`:

```python
def bench_payload(app, date=None):
    bench_dir = paths(app.config)["bench"]
    dates = bench.list_dates(bench_dir)
    if not dates:
        return {"dates": [], "date": None, "engines": [], "machine": "", "items": [], "sample": [],
                "has_scores": False}
    if date and date not in dates:
        raise BadRequest("no benchmark for that date")
    date = date or dates[-1]
    data, scores, review = bench.load_round(bench_dir, date)
    items = [dict(it, scores=scores.get(it["key"], {}), review=review.get(it["key"], {})) for it in data["items"]]
    return {"dates": dates, "date": date, "engines": sorted(data["engines"]), "machine": data.get("machine", ""),
            "items": items, "sample": bench.review_sample(data, scores), "has_scores": bool(scores)}


def bench_review(app, body):
    bench_dir = paths(app.config)["bench"]
    date = str(body.get("date") or "")
    if date not in bench.list_dates(bench_dir):
        raise BadRequest("unknown benchmark date")
    data, _, _ = bench.load_round(bench_dir, date)
    key, engine = str(body.get("key") or ""), str(body.get("engine") or "")
    if key not in {it["key"] for it in data["items"]}:
        raise BadRequest("unknown item")
    if engine not in data["engines"]:
        raise BadRequest("unknown engine")
    bench.save_review(bench_dir, date, key, engine, bool(body.get("disagree")), body.get("note") or "")
    return {"ok": True}
```

In `Handler.do_GET`, before the final `else:` branch, add:

```python
            elif path == "/bench/translate":
                self._serve_static("/bench.html", "text/html; charset=utf-8")
            elif path == "/api/bench/translate":
                self._json(bench_payload(app, (q.get("date") or [""])[0]))
```

In `Handler.do_POST`, before the final `else:` branch, add:

```python
            elif path == "/api/bench/review":
                self._json(bench_review(app, body))
```

and before `        except Exception as e:` in `do_POST` add:

```python
        except BadRequest as e:
            self._json({"error": str(e)}, 400)
```

- [ ] **Step 4: The page**

```html
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Translation benchmark</title>
<link rel="icon" href="data:,">
<style>
:root { --bg: #f6f6f3; --panel: #ffffff; --text: #1c1c1e; --muted: #6c6c72; --line: #e3e3de; --warn: #a15c07; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111214; --panel: #191a1d; --text: #e9e9ec; --muted: #9b9ba3; --line: #2a2b30; --warn: #f0a83a; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 0 16px 80px; }
h1 { font-family: Georgia, "Times New Roman", serif; font-size: 20px; margin: 16px 0 6px; }
.bar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; font-size: 14px; color: var(--muted); margin-bottom: 10px; }
select, input { font: inherit; color: inherit; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.orig { font-weight: 600; }
.meta { color: var(--muted); font-size: 12px; display: block; }
.score { font-variant-numeric: tabular-nums; }
.err { color: var(--warn); }
input[type=text] { width: 100%; margin-top: 4px; padding: 3px 6px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); }
@media (max-width: 700px) { table, tbody, tr, td { display: block; } thead { display: none; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>Translation benchmark review</h1>
  <div class="bar" id="bar">Loading...</div>
  <div id="bench"></div>
</div>
<script type="module" src="/js/bench.js"></script>
</body>
</html>
```

The script path is absolute: the page lives at `/bench/translate`, so a relative `js/bench.js` would resolve to `/bench/js/bench.js`.

```javascript
'use strict';
// Review page for the translation benchmark: original, every engine's output, Claude's scores,
// and a "disagree" mark per score.
import { getJSON, post } from './api.js';

var B = { data: null, all: false };

function $(sel) { return document.querySelector(sel); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; });
}

function load(date) {
  getJSON('/api/bench/translate' + (date ? '?date=' + encodeURIComponent(date) : '')).then(function (d) {
    B.data = d;
    render();
  }).catch(function (e) { $('#bar').textContent = 'Could not load the benchmark: ' + e.message; });
}

function scoreText(s) {
  return s ? 'adequacy ' + s.adequacy + ' · fluency ' + s.fluency + (s.note ? ' · ' + s.note : '') : 'not scored';
}

function cellHTML(it, engine) {
  var tr = it.translations[engine] || { text: '', error: 'not run' };
  var s = it.scores[engine];
  var rv = it.review[engine] || {};
  var h = tr.error ? '<div class="err">' + esc(tr.error) + (tr.text ? ': ' + esc(tr.text) : '') + '</div>'
                   : '<div lang="es">' + esc(tr.text) + '</div>';
  h += '<span class="meta score">' + esc(scoreText(s)) + (tr.seconds ? ' · ' + tr.seconds + ' s' : '') + '</span>';
  if (s) {
    h += '<label class="meta"><input type="checkbox" data-disagree="' + esc(it.key) + '" data-engine="' + esc(engine) + '"' +
      (rv.disagree ? ' checked' : '') + '> I disagree with this score</label>' +
      '<input type="text" placeholder="Why (optional)" data-note="' + esc(it.key) + '" data-engine="' + esc(engine) +
      '" value="' + esc(rv.note || '') + '">';
  }
  return '<td>' + h + '</td>';
}

function render() {
  var d = B.data;
  if (!d.dates.length) {
    $('#bar').textContent = 'No benchmark yet. Run: py tools/bench_translate.py run';
    $('#bench').innerHTML = '';
    return;
  }
  $('#bar').innerHTML = '<label>Round <select id="date">' + d.dates.map(function (x) {
      return '<option' + (x === d.date ? ' selected' : '') + '>' + esc(x) + '</option>';
    }).join('') + '</select></label><span>' + esc(d.machine) + '</span><span>' +
    (d.has_scores ? d.sample.length + ' scored items to review' : 'not scored yet') + '</span>' +
    '<label><input type="checkbox" id="all"' + (B.all ? ' checked' : '') + '> Show every item (' + d.items.length + ')</label>';
  var inSample = {};
  d.sample.forEach(function (k) { inSample[k] = true; });
  var list = (B.all || !d.has_scores) ? d.items : d.items.filter(function (it) { return inSample[it.key]; });
  var h = '<table><thead><tr><th>Original</th>' + d.engines.map(function (e) { return '<th>' + esc(e) + '</th>'; }).join('') +
    '</tr></thead><tbody>';
  h += list.map(function (it) {
    return '<tr><td><div class="orig" dir="auto" lang="' + esc(it.lang) + '">' + esc(it.text) + '</div><span class="meta">' +
      esc(it.lang) + ' · ' + esc(it.field) + ' · ' + esc(it.source_id) + '</span></td>' +
      d.engines.map(function (e) { return cellHTML(it, e); }).join('') + '</tr>';
  }).join('');
  $('#bench').innerHTML = h + '</tbody></table>';
}

function saveReview(key, engine) {
  var sel = '[data-engine="' + CSS.escape(engine) + '"]';
  var box = document.querySelector('[data-disagree="' + CSS.escape(key) + '"]' + sel);
  var note = document.querySelector('[data-note="' + CSS.escape(key) + '"]' + sel);
  var body = { date: B.data.date, key: key, engine: engine, disagree: !!(box && box.checked), note: note ? note.value : '' };
  post('/api/bench/review', body).then(function () {
    B.data.items.forEach(function (it) { if (it.key === key) it.review[engine] = { disagree: body.disagree, note: body.note }; });
  }).catch(function (e) { $('#bar').insertAdjacentText('beforeend', ' · not saved: ' + e.message); });
}

document.addEventListener('change', function (e) {
  var t = e.target;
  if (t.id === 'date') { load(t.value); return; }
  if (t.id === 'all') { B.all = t.checked; render(); return; }
  if (t.dataset && t.dataset.disagree) saveReview(t.dataset.disagree, t.dataset.engine);
});

var timers = {};
document.addEventListener('input', function (e) {
  var t = e.target;
  if (!(t.dataset && t.dataset.note)) return;
  var id = t.dataset.note + '|' + t.dataset.engine;
  clearTimeout(timers[id]);
  timers[id] = setTimeout(function () { saveReview(t.dataset.note, t.dataset.engine); }, 600);
});

load('');
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest tests.test_bench_api -v`, then `py -m unittest discover -s tests`.
Expected: all pass.

- [ ] **Step 6: Verify in a browser**

Run `node --check static/js/bench.js`; search it for `??` and `?.` (expected: none in code).

Build a scratch round with the fake engine, never in `data/` (Git Bash):

```bash
mkdir -p "$TMP/nc-bench"
cp data/news.db "$TMP/nc-bench/news.db"
printf '{"data_dir": "%s", "port": 8799, "worker": false}\n' "$(cygpath -m "$TMP/nc-bench")" > "$TMP/nc-bench/config.json"
py tools/bench_translate.py run --engines fake --per-lang 3 --config "$TMP/nc-bench/config.json"
py -c "import json,sys,glob; p=sorted(glob.glob(sys.argv[1]+'/bench/translate-*.json'))[0]; d=json.load(open(p,encoding='utf-8')); s={it['key']:{'fake':{'adequacy':3,'fluency':4,'note':'sample'}} for it in d['items'][:30]}; json.dump({'scores':s},open(p.replace('.json','-scores.json'),'w',encoding='utf-8'))" "$TMP/nc-bench"
py server.py --config "$TMP/nc-bench/config.json"
```

Copying `news.db` while the real server is running can give a torn copy in WAL mode; stop the real server first, or skip the copy and let the scratch server collect for two minutes before the `run` command. Run the server in the background and open `http://127.0.0.1:8799/bench/translate` with the console open. Check:

1. The bar shows the round date, the machine string, `25 scored items to review` (or fewer if the archive is small) and a "Show every item" box.
2. Each row shows the original with `dir="auto"` and its language code, then a `fake` column with `[xx>es] ...`, `adequacy 3 · fluency 4 · sample` and the time.
3. Tick "I disagree" on one row and type a note; reload the page: the box is still ticked and the note is still there. `$TMP/nc-bench/bench/translate-<date>-review.json` contains them.
4. "Show every item" lists all items; unscored ones say `not scored` and have no checkbox.
5. Arabic or Persian originals, if present, render right to left.
6. The console shows no errors in any of these steps, and the page is readable at phone width (narrow the window: rows stack).

Stop the server and delete `$TMP/nc-bench`.

- [ ] **Step 7: Commit**

```bash
git add newscentral/api.py static/bench.html static/js/bench.js tests/test_bench_api.py
git commit -m "Add the /bench/translate page to review benchmark scores" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Documentation and spec alignment

**Files:**
- Modify: `CLAUDE.md`, `ROADMAP.md`, `docs/superpowers/specs/2026-11-11-translation-design.md`

- [ ] **Step 1: `CLAUDE.md`**

In the Run block, after the `--lan` line add:

```
python server.py --no-worker  # do not start the background worker (translation)
```

Replace the paragraph that starts `Reader at http://127.0.0.1:8787. Runtime data goes to` with:

```
Reader at http://127.0.0.1:8787. Runtime data goes to `data/` (SQLite `news.db` with archive, saved items, notes and translations; `sources_status.json` written after every fetch cycle; `models/` with downloaded translation models; `bench/` with benchmark rounds). `data/` is gitignored and must never be committed: the repository is public. The database runs in WAL mode, so `news.db-wal` and `news.db-shm` sit next to it: a backup must copy all three files with the server stopped, or use SQLite's backup API (`sqlite3 data/news.db ".backup backup.db"`).
```

In the Architecture section, after the bullet that starts `- \`NewsCentral.command\``, add:

```
- Worker: `python -m newscentral.worker`, started by the server after it binds its port and migrates the database, restarted after a crash (10 s doubling to 10 min, reset after 10 min of running), stopped with the server. Exit code 3 means schema mismatch: not restarted. Jobs (`newscentral/jobs.py`) implement `available()`, `pending()`, `run()`, `save()`; the first is `translate`. Status in the `worker_status` table, shown in `/api/status` and the Sources view.
- Translation (`newscentral/translate/`): items in languages outside `read_langs` get Spanish titles and summaries in `translations` (migration 6), titles first, newest first; three failures skip an item until `tools/retranslate.py` clears it. Engines `nllb-600m`, `m2m100-418m`, `opus-mt` on CTranslate2 (optional, `requirements-translate.txt`, Python 3.10+), chosen per source language by `translate_engine` in `config.json`; `fake` is for tests and development. Models: `py tools/get_models.py <engine>`; check one with `py -m newscentral.translate.selfcheck <engine>`. Benchmark: `py tools/bench_translate.py run|summarize`, review at `/bench/translate`.
- API additions: `/api/items`, `/api/search` and `/api/saved` carry `tr: {title, summary, engine}`; `/api/items?tr_since=` returns `translated` and `tr_seen` so open pages receive late translations; `/api/stats` has `translation` counts; `/api/status` has `worker` and `worker_enabled`. The search index `translations_fts` is managed by `Store.ensure_fts()` with `items_fts`.
```

In Hard constraints, replace `- One SQLite file is the whole state. Schema changes go through numbered migrations (to be introduced in Phase 1).` with:

```
- One SQLite file is the whole state (WAL mode). Schema changes go through numbered migrations, run only by the server process; the worker and the tools open the file with `Store(path, migrate=False)` and refuse a different schema version.
```

- [ ] **Step 2: `ROADMAP.md`**

In Phase 3, before `- [ ] Benchmark a small multilingual embedding model on the Mac and on the Pi before choosing one`, add:

```
- [x] Translation into Spanish of titles and summaries in unread languages (spec 2a): worker process, local engines, queue, search over translations, reader display
- [ ] Choose the translation engine per language from the benchmark (after three days of batch 1b), on this machine and again on the Pi
```

In Known issues, replace the line `- Chinese, Japanese and Korean text is not segmented by the search index; searching those languages matches whole runs only until translated text exists.` with:

```
- Chinese, Japanese and Korean originals are not segmented by the search index; a Spanish query finds them through their translation.
- Translation needs Python 3.10+ and the optional CTranslate2 and sentencepiece packages. ARM64 wheels are published on PyPI but not yet installed or run on the Pi; the Pi's throughput (seconds per item) is unmeasured.
- Opus-MT pivots through English for most languages unless `tools/get_models.py opus-mt --convert` builds the direct models (needs transformers and torch).
```

- [ ] **Step 3: Align the spec**

In `docs/superpowers/specs/2026-11-11-translation-design.md`:

- Status line: `Status: implemented <date>; engine choice pending the benchmark.`
- Section 1, job bullet: add `save(store, batch, results)` (writes the results in one transaction and returns the number done) and "`available()` sets `job.reason`".
- Section 1, supervision bullet: add "The worker exits with code 3 on a schema mismatch; the supervisor does not restart it. The child runs with the repository as working directory and writes to the server's log file when there is one."
- Section 2, dependencies bullet: replace the wheel sentences with: "Wheels for `cp314` `win_amd64` and `manylinux_2_28_aarch64` are published on PyPI (checked 2026-11-11; `ctranslate2` 4.9.0, `sentencepiece` 0.2.3). They need Python 3.10+; the core stays 3.8. ARM64 wheels still have to be installed and run on the Pi."
- Section 2, candidates: M2M100 "downloaded as float16 (`michaelfeil/ct2fast-m2m100_418M`), run as int8"; Opus-MT: the routes of deviation 5 and the `--convert` option; NLLB from `JustFrederik/nllb-200-distilled-600M-ct2-int8`.
- Section 2, interface bullet: "Engines receive canonical text and return plain text; the job applies `text.canonical` (not `normalize`, whose tag stripper would delete text such as `<2%`)."
- Section 3: add the index `items_lang_seen ON items(lang, first_seen)`; add "Search: every term must be in the original or in one translated field; ranking takes the best `bm25` of both indexes."
- Section 4: add "and items given up after three failures" to the Collection line.
- Section 6: add "`/api/items` also returns `tr_seen` and, with `tr_since`, `translated` (translations created since then for items in the live window), so a page that is already open receives translations that finish later."
- Build order: note that step 9 shipped before step 8, which waits for three days of batch 1b.

- [ ] **Step 4: Full verification before merging**

Run each and read the output:

```bash
py -m unittest discover -s tests
py server.py --check-sources
py server.py --check
```

Expected: `OK`; `0 problem(s)`; the check summary close to the previous run, every new failure explained.

Search the frontend for forbidden operators: no `??` or `?.` in `static/js/`. Search the new test files for non-ASCII characters: `grep -nP '[^\x00-\x7F]' tests/test_wal.py tests/test_worker.py tests/test_supervisor.py tests/test_worker_status_api.py tests/test_translate_queue.py tests/test_translate_job.py tests/test_retranslate.py tests/test_translation_api.py tests/test_engines.py tests/test_get_models.py tests/test_bench.py tests/test_bench_api.py` (expected: no output).

Confirm nothing private is staged: `git status --short` shows no `data/` path (no `data/models/`, no `data/bench/`) and no `config.json`.

Start the server on the default port with the real engine, load the reader, and walk through Feed (translated items, original line, "Resumen original"), Archive search with a Spanish word that only a translation contains, Saved and "Copy for Claude", and Sources (worker line, translation line) with the console open.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md ROADMAP.md docs/superpowers/specs/2026-11-11-translation-design.md
git commit -m "Document the worker, translation and WAL backups; align the spec" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then use superpowers:finishing-a-development-branch to merge `feature/translation` into `main` after the user confirms. Task 16 can run after the merge.

---

### Task 16: Run the benchmark, judge, choose and configure (hand-over, no code)

This task is for the orchestrator and the user. It writes no code. Start it when the Collection block shows at least three full days (UTC) of batch 1b volume in the "Other languages" column.

**Files:**
- Modify: `config.json` (local, gitignored, never committed), `ROADMAP.md` (results line)
- Written under `data/bench/` (never committed): `translate-<date>.json`, `translate-<date>-scores.json`, `translate-<date>-review.json`

- [ ] **Step 1: Prepare the machine**

Install the engines and every model (Task 12, Step 10), and run the three self-checks. Restart the server with `py server.py --no-worker` so the worker does not compete for the CPU while timing.

- [ ] **Step 2: Run the benchmark**

Run: `py tools/bench_translate.py run`. Expected: `N units in M languages; engines: m2m100-418m, nllb-600m, opus-mt`, then one line per engine with errors and seconds per title and per summary, then `Wrote ...\data\bench\translate-<date>.json`. About 200 items (up to 15 per language, titles and summaries) take minutes per engine on this laptop; the times are the per-machine cost.

- [ ] **Step 3: Claude judges, in one Claude Code session on the subscription**

Open a Claude Code session in the repository and give it this brief:

> Read `data/bench/translate-<date>.json`. For every item and every engine in `items[].translations` whose `error` is empty, score the Spanish translation of `text` (source language `lang`, field `field`) from 1 to 5 for adequacy (is the meaning of the original preserved: facts, names, numbers, negations, who did what) and fluency (is it natural, correct Spanish as a Spanish newspaper would write it). Score each engine independently; do not look at the engine name when it can be avoided. Add a short `note` only when a score is 2 or lower, naming the error. Write `data/bench/translate-<date>-scores.json` as `{"date": "<date>", "judge": "Claude, Claude Code session", "scores": {"<key>": {"<engine>": {"adequacy": n, "fluency": n, "note": ""}}}}`, with `<key>` the item's `key`. Do not change the benchmark file. Work in chunks if needed and write the file once at the end.

Check that the file parses: `py -c "import json; json.load(open('data/bench/translate-<date>-scores.json', encoding='utf-8'))"`.

- [ ] **Step 4: The user reviews a sample**

Start `py server.py` and open `http://127.0.0.1:8787/bench/translate`. The page shows 25 scored items (a stable sample). The user reads original and translations side by side and ticks "I disagree" on every score they think is wrong, with a short reason. Expected: the review file appears under `data/bench/`.

If the user disagrees with more than about one score in five, the judging is not trustworthy for this decision: stop, discuss with the user, and either re-judge with a sharper brief or let the user score a larger sample by hand. Do not choose engines on scores the user rejected.

- [ ] **Step 5: Choose and configure**

Run: `py tools/bench_translate.py summarize`. Expected: a table of mean adequacy, fluency, seconds per item and count per language and engine, the chosen engine marked with `<`, the review count, and a line `Put this in config.json: "translate_engine": {...}`.

Present the table to the user, with the weak points stated plainly (for example a language where every engine scores below 3, or a choice decided by a 0.1 difference on a handful of items). After the user agrees, put the `translate_engine` value in `config.json` and restart the server without `--no-worker`.

If the new mapping changes the engine of languages already translated, put them back in the queue: `py tools/retranslate.py --engine <old engine> --lang <code>` for each changed language. The worker retranslates them, titles first.

- [ ] **Step 6: Watch one day and record the result**

After 24 hours, open Sources: the translation line should show pending titles near zero. If titles keep growing, the machine cannot keep up: tell the user (the fallback in the spec is translating summaries on demand), with the measured seconds per item and the daily volume.

Add one line to `ROADMAP.md` under the Phase 3 translation entry, for example `Benchmark <date> on the Windows laptop: <engine> by default, <exceptions>; <n> items/day translated, backlog <state>.`, mark the engine-choice entry done for this machine, and commit:

```bash
git add ROADMAP.md
git commit -m "Record the translation benchmark result" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Repeat Steps 1 to 6 on the Raspberry Pi before the move (Phase 4).

---

## Self-review against the spec

| Spec section | Tasks |
|---|---|
| Goals: every unread-language item gets a Spanish title and summary | 6, 7, 12 |
| Goals: reader, archive search, saved view show translation with original | 8, 9, 10 |
| Goals: never blocks collection or the reader; model crash never stops the server | 3, 4 (separate process, supervisor) |
| Goals: engine replaceable, archive retranslatable without schema changes | 6 (registry), 8 (`tools/retranslate.py`) |
| Goals: framework runs 2b and 2c jobs later | 3 (`Job`, `build_jobs`) |
| 1. Worker process and job framework, sleep 30 s | 3 |
| 1. Supervision, backoff 10 s to 10 min, reset after 10 min, terminate on shutdown, `--no-worker`, `worker` key | 2, 4 |
| 1. Status heartbeat `worker_status`, shown in `/api/status` and Sources | 1, 3, 5 |
| 1. Resource limits `translate_batch`, `worker_threads` | 2, 7, 12 |
| 1. SQLite with two processes: WAL, busy timeout, migrations only in the server, version check, short transactions, backup note | 1, 3, 6, 15 |
| 2. Engines: package layout, interface, candidates, optional dependencies, models, `translate_engine` | 2, 6, 11, 12 |
| 3. Data: migration 6, `field`/`lang`, originals unchanged, retranslating, `translations_fts`, search without duplicates | 1, 6, 8 |
| 4. Queue and priority, failures, Collection line | 6, 7 |
| 5. Benchmark: sample, timings, file, judging, review page, decision, when to run | 13, 14, 16 |
| 6. Reader: `tr` fields, display, language filter, client search, export | 9, 10 |
| 7. Error handling: unavailable, crash, locked database, empty or long output, version mismatch | 3, 4, 7 |
| 8. Testing: fake engine, queue, worker, supervision, WAL, search, frontend, engines only by hand | every task; self-checks in 12 |
| Risks: Pi throughput, ARM64 wheels, volume, distant pairs, licence, Claude as judge | 15 (Known issues), 16 (review gate, Pi rerun) |
| Build order 1 to 7 and 9; 8 | Tasks 1 to 14 follow 1 to 7; 15 is step 9; 16 is step 8 |

Placeholder scan: no open items or cross-task shortcuts; every code step has the full code or an exact old/new pair. Python 3.8 syntax in every block (no walrus, no `match`, no built-in generics). JavaScript: no `??` or `?.`. Test sources use `\uXXXX` escapes for non-ASCII text.

Facts marked **to verify** (by the self-checks in Task 12): that sentencepiece pieces with the token conventions above match the quality of the `transformers` tokenizers; that `ct2-transformers-converter --copy_files` copies `source.spm` and `target.spm` from a hub model when converting Opus-MT (`--convert` path only); that Traditional-script Chinese and Latin-script Serbian sources translate acceptably with `zho_Hans` and `srp_Cyrl`.

## Names defined across tasks

- Task 1, `store.py`: `BUSY_TIMEOUT_MS`, `SchemaMismatch`, `Store(path, fts=True, migrate=True)`, `Store.journal_mode`, `Store._check_version(fts)`, `Store.set_worker_status(job, state, last_batch=None, done_last_hour=0, last_error="", now=None)`, `Store.worker_status()`; migration 6 tables `translations`, `translation_failures`, `worker_status`, index `items_lang_seen`.
- Task 2, `config.py`: `TARGET_LANG`, `DEFAULTS["worker"|"translate_batch"|"worker_threads"|"translate_engine"]`, `worker_threads(cfg)`, `paths(cfg)["models"|"bench"]`.
- Task 3: `jobs.Job` (`name`, `batch_size`, `reason`, `available()`, `pending(store, limit)`, `run(batch)`, `save(store, batch, results)`); `supervisor.EXIT_SCHEMA`; `worker.IDLE_SECONDS`, `worker.log(*parts)`, `worker.Worker(store, jobs, idle_seconds=IDLE_SECONDS, clock=time.time)` with `stop()`, `done_last_hour(name, now)`, `status(job, state, error="")`, `step()`, `run_forever()`, `stopping`; `worker.install_signal_handlers(worker)`; `worker.build_jobs(cfg, p)`; `worker.main(argv=None)`.
- Task 4: `supervisor.next_delay(previous, ran_for, min_delay, max_delay, reset_after)`, `supervisor.Supervisor(cmd, cwd=None, stdout=None, min_delay=10, max_delay=600, reset_after=600, log=_log)` with `run()`, `stop(timeout=5)`, `proc`, `starts`, `delays`, `gave_up`; `cli.start_worker(cfg, config_path, stdout=None, start=True)`; flag `--no-worker`.
- Task 5: `api.status_payload(app)` adds `worker_enabled`, `worker`; JS `agoText(ts)`, `WORKER_CLASS`, `workerHTML()`, `S.worker`, `S.workerEnabled`.
- Task 6: `translate.base.Engine(model_dir, threads=1)` with `name`, `requires`, `version`, `missing_modules()`, `model_ready(model_dir)`, `check(model_dir)`, `supports(src, tgt)`, `translate(texts, src, tgt)`; `base.read_version(model_dir)`, `base.strip_special(tokens)`, `base.SPECIAL_TOKENS`; `fake.FakeEngine`; `registry.ENGINES`, `registry.check(name, models_dir)`, `registry.create(name, models_dir, threads=1)`, `registry.installed(models_dir, include_fake=False)`; `store.TRANSLATED_FIELDS`, `store.MAX_ATTEMPTS`, `Store._pending_sql(field, skip_langs, target, max_attempts)`, `Store.pending_translations(skip_langs, target, limit, max_attempts=MAX_ATTEMPTS)`, `Store.save_translations(results, now=None)`, `Store.translation_counts(skip_langs, target, now=None, max_attempts=MAX_ATTEMPTS)`; test helpers `TranslationCase`, `tr_ok(item_id, field, text="texto", engine="fake")`, `tr_failed(item_id, field, error="boom")`.
- Task 7: `translate.job.MAX_GROWTH`, `check_output(source_text, output)`, `TranslateJob(cfg, models_dir)` with `engine_name(src)`, `engine(name)`, `result(unit, engine, text, error)`; `api.stats_payload` adds `translation`; JS `translationHTML(tr)`.
- Task 8: `store.TR_FTS_TABLE`, `store.TR_FTS_TRIGGERS`, `store.FTS_INDEXES`, `Store.ensure_fts(wanted=True)` over both indexes, `Store.search(..., tr_lang=TARGET_LANG)`, `Store.clear_translations(engine=None, src_lang=None, target=TARGET_LANG)`; `tools/retranslate.py main(argv=None)`; test helper `load_tool(name)`.
- Task 9: `Store.translations_for(ids, lang=TARGET_LANG)`, `Store.max_translation_created(lang=TARGET_LANG)`, `Store.translated_since(after, lang, min_first_seen)`; `api.attach_translations(app, items, key="id")`; `/api/items` fields `tr_seen`, `translated`, parameter `tr_since`.
- Task 10: JS `fetchItems(since, trSince)`, `itemText(it, src)`, `titleHTML(it)`, `summaryHTML(it, open)`, `langTag(it, lang)`, `S.trSeen`; CSS `.orig`, `.orig-sum`.
- Task 11: `translate.download.HUB`, `SKIP_FILES`, `ENGINE_NAMES`, `SINGLE`, `OPUS_CONVERTED`, `OPUS_DIRECT_UNCONVERTED`, `OPUS_ROUTES`, `DownloadError`, `describe(exc, url)`, `repo_info(repo, base=HUB)`, `download_file(url, dest, log=print)`, `write_json(path, obj)`, `download_repo(repo, dest, base=HUB, log=print)`, `convert_model(model, dest, copy_files=(), revision="", run=subprocess.run, which=shutil.which)`, `opus_plan(convert)`, `get(engine, models_dir, convert=False, base=HUB, log=print, run=subprocess.run)`; `tools/get_models.py main(argv=None)`.
- Task 12: `translate.ct2.BEAM_SIZE`, `translator(model_dir, threads)`, `sentencepiece(path)`, `max_length(token_lists)`; `nllb.NLLB_CODES`, `nllb.source_tokens(pieces, src)`, `nllb.NllbEngine`; `m2m100.M2M_LANGS`, `m2m100.lang_token(lang)`, `m2m100.source_tokens(pieces, src)`, `m2m100.M2M100Engine`; `opus_mt.ROUTES_FILE`, `opus_mt.source_tokens(pieces)`, `opus_mt.load_routes(model_dir)`, `opus_mt.OpusMtEngine` with `pair_ready(pair)`; `selfcheck.SENTENCES`, `selfcheck.selfcheck(name, models_dir, threads, out=print)`, `selfcheck.main(argv=None)`.
- Task 13: `Store.sample_by_lang(skip_langs, per_lang)`; `translate.bench.DATE_RE`, `FILE_RE`, `REVIEW_SAMPLE`, `bench_path(bench_dir, date, kind="")`, `read_json(path, default=None)`, `write_json(path, obj)`, `sample_units(store, read_langs, per_lang=15, target=TARGET_LANG)`, `run_bench(units, engines, batch_size=16, clock=time.perf_counter, date=None)`, `compress(choice)`, `summarize(data, scores)`, `list_dates(bench_dir)`, `load_round(bench_dir, date)`, `review_sample(data, scores, n=REVIEW_SAMPLE)`, `save_review(bench_dir, date, key, engine, disagree, note)`; `tools/bench_translate.py main(argv=None)`.
- Task 14: `api.bench_payload(app, date=None)`, `api.bench_review(app, body)`; routes `GET /bench/translate`, `GET /api/bench/translate[?date=]`, `POST /api/bench/review`; JS `load(date)`, `scoreText(s)`, `cellHTML(it, engine)`, `render()`, `saveReview(key, engine)`.
