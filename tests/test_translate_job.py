import unittest
from unittest import mock

from newscentral import worker as worker_mod
from newscentral.config import DEFAULTS
from newscentral.translate import registry
from newscentral.translate.fake import FakeEngine
from newscentral.translate.job import TranslateJob, check_output
from newscentral.worker import Worker
from tests.helpers import ApiCase, TranslationCase


def cfg(**kw):
    c = dict(DEFAULTS, translate_engine={"default": "fake"}, translate_batch=10, worker_threads=1)
    c.update(kw)
    return c


class TestCheckOutput(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(check_output("hola", "  buenos   dias "), ("buenos dias", ""))
        self.assertEqual(check_output("hola", " "), ("", "empty output"))
        self.assertEqual(check_output("ab", "x" * 10), ("x" * 10, ""))
        text, error = check_output("ab", "x" * 11)
        self.assertEqual(text, "")
        self.assertIn("five times", error)


class TestTranslateJob(TranslationCase):
    def job(self, **kw):
        return TranslateJob(cfg(**kw), self.dir)

    def test_available_with_the_fake_engine(self):
        job = self.job()
        self.assertTrue(job.available())
        self.assertEqual(job.reason, "")

    def test_unavailable_engine_gives_a_reason(self):
        job = self.job(translate_engine={"default": "fake", "zh": "nope"})
        self.assertFalse(job.available())
        self.assertIn("nope: unknown engine", job.reason)

    def test_engine_per_language(self):
        job = self.job(translate_engine={"default": "fake", "zh": "nllb-600m"})
        self.assertEqual((job.engine_name("zh"), job.engine_name("ru")), ("nllb-600m", "fake"))

    def test_one_batch_translates_titles_then_summaries(self):
        job = self.job()
        batch = job.pending(self.store, job.batch_size)
        results = job.run(batch)
        self.assertEqual(job.save(self.store, batch, results), 5)
        rows = {(r[0], r[1]): (r[2], r[3]) for r in self.store.db.execute(
            "SELECT item_id, field, text, engine FROM translations")}
        self.assertEqual(rows[(self.ids["new_ru"], "title")],
                         ("[ru>es] Новости", "fake"))
        self.assertEqual(self.queue(), [])

    def test_empty_and_long_outputs_are_failures(self):
        self.add_at("bad1", "meduza", "EMPTY news", "", 5000)
        self.add_at("bad2", "meduza", "LONG news", "", 5001)
        job = self.job()
        batch = job.pending(self.store, 2)
        self.assertEqual([u["item_id"] for u in batch], [self.ids["bad2"], self.ids["bad1"]])
        job.save(self.store, batch, job.run(batch))
        errors = dict(self.store.db.execute("SELECT item_id, last_error FROM translation_failures").fetchall())
        self.assertEqual(errors[self.ids["bad1"]], "empty output")
        self.assertIn("five times", errors[self.ids["bad2"]])

    def test_an_engine_crash_fails_only_its_group(self):
        self.add_at("crash", "meduza", "CRASH", "", 5000)
        job = self.job()
        batch = job.pending(self.store, 10)
        results = {(r["item_id"], r["field"]): r for r in job.run(batch)}
        self.assertEqual(results[(self.ids["crash"], "title")]["error"], "RuntimeError: fake engine crash")
        self.assertEqual(results[(self.ids["new_ru"], "title")]["error"], "RuntimeError: fake engine crash")
        self.assertEqual(results[(self.ids["old_ar"], "title")]["error"], "")      # other language, other group

    def test_unsupported_language_is_a_failure(self):
        job = self.job()
        batch = job.pending(self.store, 1)
        with mock.patch.object(FakeEngine, "supports", return_value=False):
            results = job.run(batch)
        self.assertIn("does not translate ru into es", results[0]["error"])

    def test_engine_load_errors_raise_instead_of_counting_failures(self):
        job = self.job()
        batch = job.pending(self.store, 2)
        with mock.patch.object(registry, "create", side_effect=OSError("model.bin is corrupt")):
            with self.assertRaises(OSError):
                job.run(batch)

    def test_worker_drains_the_queue(self):
        job = self.job(translate_batch=2)
        w = Worker(self.store, [job], idle_seconds=0.01)
        while w.step():
            pass
        self.assertEqual(self.queue(), [])
        st = self.store.worker_status()[0]
        self.assertEqual((st["job"], st["state"], st["done_last_hour"]), ("translate", "idle", 5))

    def test_build_jobs(self):
        jobs = worker_mod.build_jobs(cfg(), {"models": self.dir})
        self.assertEqual([j.name for j in jobs], ["translate"])


class TestBacklogInStats(ApiCase):
    def test_stats_carry_translation_counts(self):
        self.add("ahram", "خبر", "https://example.com/ar", "ملخص")
        _, body = self.get("/api/stats?days=30")
        self.assertEqual(body["translation"],
                         {"pending_titles": 1, "pending_summaries": 1, "done_24h": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
