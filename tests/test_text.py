# tests/test_text.py
import unicodedata
import unittest

from newscentral import text

ZWNJ, ZWJ = "\u200c", "\u200d"
PERSIAN = "\u0645\u06cc" + ZWNJ + "\u062e\u0648\u0627\u0647\u0645"      # "mi-khaham" with ZWNJ
DEVANAGARI = "\u0915\u094d" + ZWJ + "\u0937"                           # conjunct forced with ZWJ
ARABIC = "\u0627\u0644\u0623\u062e\u0628\u0627\u0631"
CHINESE = "\u4e2d\u56fd\u7ecf\u6d4e\u65b0\u95fb"
JAPANESE = "\u65e5\u672c\u306e\u30cb\u30e5\u30fc\u30b9"
KOREAN = "\ud55c\uad6d \ub274\uc2a4"
CYRILLIC = "\u041d\u043e\u0432\u043e\u0441\u0442\u0438"
SAMPLES = [PERSIAN, DEVANAGARI, ARABIC, CHINESE, JAPANESE, KOREAN, CYRILLIC, "Caf\u00e9 del d\u00eda"]


class TestNormalize(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(text.normalize(None), "")
        self.assertEqual(text.normalize(""), "")

    def test_strips_markup_and_entities(self):
        self.assertEqual(text.normalize("<p>Hello &amp; <b>world</b></p>"), "Hello & world")
        self.assertEqual(text.normalize("a<script>x()</script>b"), "a b")

    def test_nfc(self):
        self.assertEqual(text.normalize("Cafe\u0301"), "Caf\u00e9")
        decomposed_korean = unicodedata.normalize("NFD", KOREAN)
        self.assertEqual(text.normalize(decomposed_korean), KOREAN)

    def test_removes_invisible_characters(self):
        self.assertEqual(text.normalize("\ufeffa\u200bb\u00adc\u2060d"), "abcd")
        self.assertEqual(text.normalize("\u202eabc\u202c \u2066def\u2069\u200e\u200f"), "abc def")
        self.assertEqual(text.normalize("a\x00b\x1fc\x7fd"), "abcd")

    def test_keeps_joiners(self):
        self.assertEqual(text.normalize(PERSIAN), PERSIAN)
        self.assertEqual(text.normalize(DEVANAGARI), DEVANAGARI)

    def test_whitespace(self):
        self.assertEqual(text.normalize("  a\u00a0b\u2028c\u2029d\t\n e\u3000f "), "a b c d e f")

    def test_scripts_survive_unchanged(self):
        for s in SAMPLES:
            self.assertEqual(text.normalize(s), s)

    def test_truncates_on_word_boundary_within_limit(self):
        out = text.normalize("one two three four five six seven", 20)
        self.assertLessEqual(len(out), 20)
        self.assertTrue(out.endswith("\u2026"))
        self.assertEqual(out, "one two three four\u2026")

    def test_truncates_text_without_spaces(self):
        out = text.normalize(CHINESE * 10, 10)
        self.assertEqual(len(out), 10)
        self.assertTrue(out.endswith("\u2026"))

    def test_truncation_does_not_split_a_combining_sequence(self):
        syllable = "\u0915\u093f"            # ka + vowel sign i (a combining mark)
        out = text.normalize(syllable * 10, 6)
        body = out[:-1]
        self.assertTrue(out.endswith("\u2026"))
        self.assertEqual(body, syllable * (len(body) // 2))

    def test_idempotent(self):
        for s in SAMPLES + ["one two three four five six seven", syllable_text()]:
            for limit in (None, 6, 20):
                once = text.normalize(s, limit)
                self.assertEqual(text.normalize(once, limit), once)

    def test_canonical_leaves_angle_brackets_alone(self):
        s = "inflation <2% and growth >3%"
        self.assertEqual(text.canonical(s), s)


def syllable_text():
    return "\u0915\u093f" * 10


if __name__ == "__main__":
    unittest.main()
