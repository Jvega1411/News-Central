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
