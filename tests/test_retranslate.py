import contextlib
import io
import json
import os
import unittest

from tests.helpers import TranslationCase, load_tool, tr_failed, tr_ok


class TestClearTranslations(TranslationCase):
    def setUp(self):
        super().setUp()
        self.store.save_translations([
            tr_ok(self.ids["new_ru"], "title", engine="nllb-600m"),
            tr_ok(self.ids["old_ar"], "title", engine="nllb-600m"),
            tr_ok(self.ids["old_ar"], "summary", engine="opus-mt")])
        for _ in range(3):
            self.store.save_translations([tr_failed(self.ids["new_ru"], "summary"),
                                          tr_failed(self.ids["ar_no_summary"], "title")])

    def count(self, table):
        return self.store.db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]

    def test_one_engine_goes_back_to_the_queue(self):
        self.assertEqual(self.store.clear_translations("nllb-600m"), (2, 2))
        self.assertEqual(self.count("translations"), 1)
        self.assertIn(self.key("new_ru", "title"), self.queue())
        self.assertIn(self.key("ar_no_summary", "title"), self.queue())     # failures cleared too

    def test_limited_to_one_source_language(self):
        self.assertEqual(self.store.clear_translations("nllb-600m", "ar"), (1, 1))
        self.assertNotIn(self.key("new_ru", "title"), self.queue())
        self.assertNotIn(self.key("new_ru", "summary"), self.queue())

    def test_failures_only(self):
        self.assertEqual(self.store.clear_translations(None), (0, 2))
        self.assertEqual(self.count("translations"), 3)


class TestRetranslateTool(TranslationCase):
    def setUp(self):
        super().setUp()
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title", engine="nllb-600m")])
        self.config = os.path.join(self.dir, "config.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": self.dir}, f)
        self.tool = load_tool("retranslate")

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.tool.main(["--config", self.config] + list(args))
        return code, out.getvalue()

    def test_engine(self):
        code, out = self.run_tool("--engine", "nllb-600m")
        self.assertEqual(code, 0)
        self.assertIn("1 translation(s) deleted", out)
        self.assertIn(self.key("new_ru", "title"), self.queue())

    def test_engine_or_failures_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.tool.main(["--config", self.config])

    def test_missing_database(self):
        os.remove(os.path.join(self.dir, "config.json"))
        other = os.path.join(self.dir, "other")
        os.makedirs(other)
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": other}, f)
        code, out = self.run_tool("--failures")
        self.assertEqual(code, 1)
        self.assertIn("does not exist yet", out)


if __name__ == "__main__":
    unittest.main()
