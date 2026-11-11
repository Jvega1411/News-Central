import gzip
import os
import shutil
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
import zlib

from .config import FETCH_TIMEOUT, MAX_BYTES

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15 NewsCentral/1.0")
ALT_USER_AGENT = "NewsCentral/1.0 (personal RSS reader)"
FEED_ACCEPT = ("application/rss+xml, application/atom+xml, application/rdf+xml, "
               "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5")
BLOCK_CODES = (401, 403, 406, 429, 503)
CURL = shutil.which("curl")


def make_ssl_context():
    ctx = ssl.create_default_context()
    # python.org builds on macOS ship without CA certificates; the system bundle fixes that.
    for path in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl@3/cert.pem",
                 "/usr/local/etc/openssl@1.1/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.exists(path):
            try:
                ctx.load_verify_locations(path)
            except Exception:
                pass
    try:
        import certifi  # optional
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


SSL_CTX = make_ssl_context()


class _Redirects(urllib.request.HTTPRedirectHandler):
    # Python < 3.11 does not follow 308 redirects, which many news CDNs use.
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, 307, msg, headers)


OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=SSL_CTX), _Redirects())


class NotModified(Exception):
    pass


def http_get(url, etag=None, lastmod=None, accept=None, ua=None):
    headers = {
        "User-Agent": ua or USER_AGENT,
        "Accept": accept or FEED_ACCEPT,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en, es;q=0.9, *;q=0.5",
    }
    if etag:
        headers["If-None-Match"] = etag
    if lastmod:
        headers["If-Modified-Since"] = lastmod
    req = urllib.request.Request(url, headers=headers)
    try:
        with OPENER.open(req, timeout=FETCH_TIMEOUT) as resp:
            data = resp.read(MAX_BYTES)
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            final_url = resp.geturl()
            new_etag = resp.headers.get("ETag")
            new_lastmod = resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified()
        raise
    if encoding == "gzip" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif encoding == "deflate":
        try:
            data = zlib.decompress(data)
        except zlib.error:
            data = zlib.decompress(data, -zlib.MAX_WBITS)
    return data, final_url, new_etag, new_lastmod


def curl_get(url, ua, accept=None):
    """Second opinion for sites that refuse Python's HTTP client."""
    if not CURL:
        raise RuntimeError("curl not available")
    cmd = [CURL, "-sS", "-L", "--compressed", "-m", str(FETCH_TIMEOUT), "-A", ua,
           "-H", "Accept: " + (accept or FEED_ACCEPT), "-H", "Accept-Language: en, es;q=0.9",
           "-w", "\n%{http_code} %{url_effective}", url]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=FETCH_TIMEOUT + 5)
    if proc.returncode != 0:
        raise RuntimeError("curl: " + proc.stderr.decode("utf-8", "ignore").strip()[:100])
    body, _, tail = proc.stdout.rpartition(b"\n")
    code, _, final_url = tail.decode("utf-8", "ignore").partition(" ")
    if code != "200":
        raise urllib.error.HTTPError(url, int(code or 0), "curl", None, None)
    return body, final_url or url, None, None


def get_with_fallback(url, accept=None, etag=None, lastmod=None, check=None):
    """GET url; if the site blocks us (403 etc. or a bot-check page), retry with other clients.
    check(data, final_url) parses the body and raises ValueError("...web page...") on a bot page."""
    try:
        data, final_url, et, lm = http_get(url, etag, lastmod, accept)
        return (check(data, final_url) if check else data), final_url, et, lm
    except NotModified:
        raise
    except urllib.error.HTTPError as e:
        if e.code not in BLOCK_CODES:
            raise
        first = e
    except ValueError as e:
        if "web page" not in str(e):
            raise
        first = e
    attempts = (lambda: http_get(url, accept=accept, ua=ALT_USER_AGENT),
                lambda: curl_get(url, USER_AGENT, accept),
                lambda: curl_get(url, ALT_USER_AGENT, accept))
    for attempt in attempts:
        try:
            data, final_url, et, lm = attempt()
            return (check(data, final_url) if check else data), final_url, et, lm
        except Exception:
            continue
    raise first


def short_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %s" % exc.code
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return "SSL error: %s" % (getattr(reason, "reason", None) or reason)
        if isinstance(reason, socket.timeout):
            return "timeout"
        return str(reason)[:120]
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    return ("%s: %s" % (type(exc).__name__, exc))[:160]
