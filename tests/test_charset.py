import unittest

from newscentral import text
from newscentral.feeds import parse_feed

RUSSIAN = "Новости дня"
CHINESE = "中国经济新闻"
JAPANESE = "日本のニュース"
ARABIC = "الأخبار"
SPANISH = "España"


def feed(title, encoding, declare):
    head = '<?xml version="1.0" encoding="%s"?>' % encoding if declare else ""
    xml = "%s<rss><channel><item><title>%s</title><link>https://example.com/x</link></item></channel></rss>" % (
        head, title)
    return xml.encode(encoding)


class TestCharsetFromHeader(unittest.TestCase):
    def test_parses_content_type(self):
        f = text.charset_from_content_type
        self.assertEqual(f("text/xml; charset=windows-1251"), "windows-1251")
        self.assertEqual(f('application/rss+xml; charset="GBK"'), "GBK")
        self.assertIsNone(f("text/xml"))
        self.assertIsNone(f(None))


class TestDecodeBytes(unittest.TestCase):
    def test_utf8_wins_over_a_wrong_latin1_header(self):
        data = SPANISH.encode("utf-8")
        self.assertEqual(text.decode_bytes(data, "ISO-8859-1"), SPANISH)

    def test_header_charset_used_when_not_utf8(self):
        self.assertEqual(text.decode_bytes(RUSSIAN.encode("windows-1251"), "windows-1251"), RUSSIAN)

    def test_bom(self):
        self.assertEqual(text.decode_bytes(RUSSIAN.encode("utf-16")), RUSSIAN)
        self.assertEqual(text.decode_bytes(b"\xef\xbb\xbfabc"), "abc")

    def test_latin_fallback_only_when_allowed(self):
        data = SPANISH.encode("cp1252")
        self.assertEqual(text.decode_bytes(data), SPANISH)
        with self.assertRaises(text.UndecodableFeed):
            text.decode_bytes(RUSSIAN.encode("windows-1251"), latin_fallback=False)

    def test_unknown_declared_charset_is_skipped(self):
        self.assertEqual(text.decode_bytes(b"abc", "no-such-charset"), "abc")


class TestParseFeedCharsets(unittest.TestCase):
    def check(self, title, encoding, declare):
        items = parse_feed(feed(title, encoding, declare), "https://example.com/",
                           http_charset=None if declare else encoding, latin_fallback=False)
        self.assertEqual([i["title"] for i in items], [title])

    def test_declared_in_xml(self):
        self.check(RUSSIAN, "windows-1251", True)
        self.check(CHINESE, "gbk", True)
        self.check(JAPANESE, "shift_jis", True)
        self.check(ARABIC, "windows-1256", True)

    def test_declared_only_in_http_header(self):
        self.check(RUSSIAN, "windows-1251", False)
        self.check(CHINESE, "gbk", False)
        self.check(JAPANESE, "shift_jis", False)
        self.check(ARABIC, "windows-1256", False)

    def test_non_latin_without_charset_fails_instead_of_storing_garbage(self):
        with self.assertRaises(text.UndecodableFeed):
            parse_feed(feed(RUSSIAN, "windows-1251", False), "https://example.com/",
                       http_charset=None, latin_fallback=False)

    def test_latin_source_keeps_the_lenient_fallback(self):
        items = parse_feed(feed(SPANISH + " hoy", "cp1252", False), "https://example.com/")
        self.assertEqual(items[0]["title"], SPANISH + " hoy")


if __name__ == "__main__":
    unittest.main()
