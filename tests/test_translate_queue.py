import sqlite3
import unittest

from newscentral.translate import registry
from newscentral.translate.fake import FakeEngine
from tests.helpers import TranslationCase, tr_failed, tr_ok


class TestQueue(TranslationCase):
    def test_titles_first_then_summaries_newest_first(self):
        self.assertEqual(self.queue(), [
            self.key("new_ru", "title"), self.key("old_ar", "title"), self.key("ar_no_summary", "title"),
            self.key("new_ru", "summary"), self.key("old_ar", "summary")])

    def test_unit_fields(self):
        unit = self.store.pending_translations(self.READ, "es", 1)[0]
        self.assertEqual(unit, {"item_id": self.ids["new_ru"], "field": "title", "lang": "ru",
                                "text": "Новости", "first_seen": 2000})

    def test_limit_is_filled_with_titles_first(self):
        self.assertEqual(self.queue(2), [self.key("new_ru", "title"), self.key("old_ar", "title")])
        self.assertEqual(self.queue(4)[3], self.key("new_ru", "summary"))

    def test_read_languages_and_unknown_languages_are_excluded(self):
        self.store.db.execute("UPDATE items SET lang = NULL WHERE id = ?", (self.ids["new_ru"],))
        self.store.db.commit()
        ids = {item_id for item_id, _ in self.queue()}
        self.assertEqual(ids, {self.ids["old_ar"], self.ids["ar_no_summary"]})

    def test_translated_fields_leave_the_queue(self):
        self.assertEqual(self.store.save_translations([tr_ok(self.ids["new_ru"], "title")]), 1)
        self.assertNotIn(self.key("new_ru", "title"), self.queue())
        self.assertIn(self.key("new_ru", "summary"), self.queue())

    def test_three_failures_stop_the_retries(self):
        for _ in range(2):
            self.store.save_translations([tr_failed(self.ids["new_ru"], "title")])
        self.assertIn(self.key("new_ru", "title"), self.queue())
        self.store.save_translations([tr_failed(self.ids["new_ru"], "title", "last one")])
        self.assertNotIn(self.key("new_ru", "title"), self.queue())
        row = self.store.db.execute("SELECT attempts, last_error FROM translation_failures").fetchone()
        self.assertEqual((row[0], row[1]), (3, "last one"))

    def test_success_clears_the_failure_count(self):
        self.store.save_translations([tr_failed(self.ids["new_ru"], "title")])
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title")])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM translation_failures").fetchone()[0], 0)

    def test_a_new_translation_replaces_the_old_one(self):
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title", "uno")])
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title", "dos", "other")])
        rows = self.store.db.execute("SELECT text, engine FROM translations").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("dos", "other")])

    def test_a_batch_is_written_in_one_transaction(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_translations([tr_ok(self.ids["new_ru"], "title"), tr_ok(self.ids["old_ar"], "body")])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM translations").fetchone()[0], 0)
        self.assertIn(self.key("new_ru", "title"), self.queue())

    def test_save_counts_successes_only(self):
        n = self.store.save_translations([tr_ok(self.ids["new_ru"], "title"),
                                          tr_failed(self.ids["old_ar"], "title")])
        self.assertEqual(n, 1)

    def test_counts(self):
        self.assertEqual(self.store.translation_counts(self.READ, "es", now=5000),
                         {"pending_titles": 3, "pending_summaries": 2, "done_24h": 0, "failed": 0})
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title")], now=4000)
        for _ in range(3):
            self.store.save_translations([tr_failed(self.ids["old_ar"], "summary")])
        self.assertEqual(self.store.translation_counts(self.READ, "es", now=5000),
                         {"pending_titles": 2, "pending_summaries": 1, "done_24h": 1, "failed": 1})
        self.assertEqual(self.store.translation_counts(self.READ, "es", now=4000 + 86401)["done_24h"], 0)

    def test_unknown_field_is_refused(self):
        with self.assertRaises(ValueError):
            self.store._pending_sql("body", self.READ, "es", 3)


class TestRegistryAndFakeEngine(unittest.TestCase):
    def test_fake_is_always_available(self):
        self.assertEqual(registry.check("fake", "no-such-dir"), "")
        engine = registry.create("fake", "no-such-dir", threads=2)
        self.assertEqual((engine.name, engine.version, engine.threads), ("fake", "1", 2))
        self.assertTrue(engine.supports("ar", "es"))
        self.assertFalse(engine.supports("es", "es"))

    def test_unknown_engine(self):
        reason = registry.check("nope", "models")
        self.assertIn("unknown engine 'nope'", reason)
        self.assertIn("fake", reason)

    def test_fake_translation_and_markers(self):
        engine = FakeEngine("unused")
        self.assertEqual(engine.translate(["hola mundo", "EMPTY x"], "ar", "es"), ["[ar>es] hola mundo", ""])
        self.assertEqual(engine.translate(["LONG"], "ar", "es"), [" ".join(["LONG"] * 6)])
        with self.assertRaises(RuntimeError):
            engine.translate(["a", "CRASH"], "ar", "es")


if __name__ == "__main__":
    unittest.main()
