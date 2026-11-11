# tests/test_feeds.py
import os
import unittest

from newscentral.feeds import parse_feed

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class TestParseFeed(unittest.TestCase):
    def test_rss(self):
        items = parse_feed(load("rss.xml"), "https://example.com/feed")
        self.assertEqual([i["title"] for i in items], ["First & foremost", "Second"])
        self.assertEqual(items[0]["link"], "https://example.com/one?id=1")   # tracking parameter removed
        self.assertEqual(items[0]["summary"], "Summary one")
        self.assertEqual(items[0]["published"], 1794297600.0)                # 2026-11-10 08:00 UTC
        self.assertIsNone(items[1]["published"])

    def test_atom_resolves_relative_links_and_offsets(self):
        items = parse_feed(load("atom.xml"), "https://example.com/feed")
        self.assertEqual(items[0]["title"], "Atom entry")
        self.assertEqual(items[0]["link"], "https://example.com/path/a")
        self.assertEqual(items[0]["published"], 1794299400.0)                # 09:30 +01:00 = 08:30 UTC

    def test_broken_xml_falls_back_to_regex(self):
        items = parse_feed(load("broken.xml"), "https://example.com/feed")
        self.assertEqual(items[0]["title"], "Broken & still readable")
        self.assertEqual(items[0]["link"], "https://example.com/broken")

    def test_html_page_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            parse_feed(b"<!DOCTYPE html><html><body>Checking your browser</body></html>", "https://example.com/")
        self.assertIn("web page", str(cm.exception))

    def test_garbage_is_unreadable(self):
        with self.assertRaises(ValueError):
            parse_feed(b"not a feed at all", "https://example.com/")


class TestTags(unittest.TestCase):
    def test_rss_tags_decoded_deduped_in_order(self):
        items = parse_feed(load("categories.xml"), "https://example.com/feed")
        self.assertEqual(items[0]["tags"], ["Economy", "World News", "Política"])

    def test_dc_subject(self):
        items = parse_feed(load("categories.xml"), "https://example.com/feed")
        self.assertEqual(items[1]["tags"], ["Trade"])

    def test_no_categories_gives_empty_list(self):
        items = parse_feed(load("categories.xml"), "https://example.com/feed")
        self.assertEqual(items[2]["tags"], [])

    def test_atom_term_preferred_over_label(self):
        items = parse_feed(load("categories-atom.xml"), "https://example.com/feed")
        self.assertEqual(items[0]["tags"], ["economy", "Trade"])

    def test_limits(self):
        cats = "".join("<category>tag%d</category>" % i for i in range(15))
        long_tag = "<category>%s</category>" % ("x" * 100)
        xml = ('<rss version="2.0"><channel><item><title>T</title><link>https://example.com/a</link>%s</item>'
               '<item><title>U</title><link>https://example.com/b</link>%s</item></channel></rss>' % (cats, long_tag))
        items = parse_feed(xml.encode("utf-8"), "https://example.com/feed")
        self.assertEqual(items[0]["tags"], ["tag%d" % i for i in range(10)])
        self.assertLessEqual(len(items[1]["tags"][0]), 60)
        self.assertTrue(items[1]["tags"][0].startswith("xxxx"))

    def test_regex_fallback_returns_tags(self):
        xml = ('<rss><channel><item><title>Broken & bad</title><link>https://example.com/b</link>'
               '<category><![CDATA[Politics]]></category><category>economy</category>'
               '<category>Economy</category><category term="Trade"/>'
               '<unclosed></item></channel>').encode("utf-8")
        items = parse_feed(xml, "https://example.com/feed")
        self.assertEqual(items[0]["tags"], ["Politics", "economy", "Trade"])


if __name__ == "__main__":
    unittest.main()
