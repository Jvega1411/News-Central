import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from newscentral import worker as worker_mod
from newscentral.jobs import Job
from newscentral.store import Store, schema_version
from newscentral.supervisor import EXIT_SCHEMA
from newscentral.worker import Worker, install_signal_handlers


class TestParentAlive(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(worker_mod.parent_alive(os.getpid()))

    def test_exited_process_is_not_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(worker_mod.parent_alive(child.pid))

    def test_loop_exits_when_parent_is_gone(self):
        w = Worker(None, [], idle_seconds=60, parent_check=lambda: False)
        t = threading.Thread(target=w.run_forever, daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive())

    def test_idle_sleep_notices_parent_death(self):
        alive = [True]
        w = Worker(None, [], idle_seconds=60, parent_check=lambda: alive[0])
        worker_mod.PARENT_CHECK_SECONDS, old = 0.05, worker_mod.PARENT_CHECK_SECONDS
        try:
            t = threading.Thread(target=w.run_forever, daemon=True)
            t.start()
            time.sleep(0.2)
            alive[0] = False
            t.join(5)
            self.assertFalse(t.is_alive())
        finally:
            worker_mod.PARENT_CHECK_SECONDS = old


class ListJob(Job):
    """Hands out the numbers in `work` in batches and records what it saved."""

    name = "count"
    batch_size = 2

    def __init__(self, work, fail_run=False, ready=True):
        super().__init__()
        self.work = list(work)
        self.fail_run = fail_run
        self.ready = ready
        self.saved = []

    def available(self):
        self.reason = "" if self.ready else "model not downloaded"
        return self.ready

    def pending(self, store, limit):
        return self.work[:limit]

    def run(self, batch):
        if self.fail_run:
            raise RuntimeError("engine exploded")
        return [n * 10 for n in batch]

    def save(self, store, batch, results):
        self.saved.extend(results)
        self.work = self.work[len(batch):]
        return len(results)


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "news.db")
        Store(self.path).db.close()                 # the "server" creates and migrates the file
        self.store = Store(self.path, migrate=False)
        self.clock = [1000.0]

    def tearDown(self):
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def worker(self, jobs):
        return Worker(self.store, jobs, idle_seconds=0.01, clock=lambda: self.clock[0])

    def status(self):
        return {r["job"]: r for r in self.store.worker_status()}


class TestStep(WorkerCase):
    def test_runs_one_batch_per_step_and_reports(self):
        job = ListJob([1, 2, 3])
        w = self.worker([job])
        self.assertTrue(w.step())
        self.assertEqual(job.saved, [10, 20])
        self.assertEqual(self.status()["count"]["state"], "running")
        self.assertEqual(self.status()["count"]["done_last_hour"], 2)
        self.assertTrue(w.step())
        self.assertFalse(w.step())
        self.assertEqual(job.saved, [10, 20, 30])
        st = self.status()["count"]
        self.assertEqual((st["state"], st["done_last_hour"], st["last_batch"]), ("idle", 3, 1000.0))

    def test_done_last_hour_forgets_old_batches(self):
        job = ListJob([1, 2, 3, 4])
        w = self.worker([job])
        w.step()
        self.clock[0] += 3601
        w.step()
        self.assertEqual(self.status()["count"]["done_last_hour"], 2)

    def test_unavailable_job_is_reported_and_skipped(self):
        job = ListJob([1], ready=False)
        w = self.worker([job])
        self.assertFalse(w.step())
        st = self.status()["count"]
        self.assertEqual((st["state"], st["last_error"]), ("unavailable", "model not downloaded"))
        self.assertEqual(job.saved, [])

    def test_failing_run_is_reported_and_the_next_job_runs(self):
        bad = ListJob([1], fail_run=True)
        bad.name = "bad"
        good = ListJob([5])
        w = self.worker([bad, good])
        self.assertTrue(w.step())
        self.assertEqual(good.saved, [50])
        self.assertEqual(self.status()["bad"]["state"], "error")
        self.assertIn("engine exploded", self.status()["bad"]["last_error"])

    def test_locked_database_during_save_is_retried_later(self):
        job = ListJob([1, 2])
        def locked(store, batch, results):
            raise sqlite3.OperationalError("database is locked")
        job.save = locked
        w = self.worker([job])
        self.assertFalse(w.step())
        self.assertEqual(self.status()["count"]["state"], "error")
        self.assertEqual(job.work, [1, 2])          # nothing consumed: the batch comes back next loop


class TestLoop(WorkerCase):
    def test_stop_ends_the_loop_while_idle(self):
        w = self.worker([ListJob([])])
        t = threading.Thread(target=w.run_forever)
        t.start()
        time.sleep(0.1)
        w.stop()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.status()["count"]["state"], "stopped")

    def test_a_signal_stops_the_loop(self):
        w = self.worker([ListJob([])])
        previous = install_signal_handlers(w)
        try:
            signal.raise_signal(signal.SIGTERM)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        self.assertTrue(w.stopping.is_set())


class TestMain(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.config = os.path.join(self.dir, "config.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": self.dir}, f)
        self.db = os.path.join(self.dir, "news.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_refuses_a_database_from_another_version(self):
        Store(self.db).db.close()
        raw = sqlite3.connect(self.db)
        raw.execute("PRAGMA user_version = %d" % (schema_version() + 1))
        raw.commit()
        raw.close()
        self.assertEqual(worker_mod.main(["--config", self.config, "--once"]), EXIT_SCHEMA)

    def test_missing_database_is_an_ordinary_error(self):
        self.assertEqual(worker_mod.main(["--config", self.config, "--once"]), 1)
        self.assertFalse(os.path.exists(self.db))

    def test_one_step_on_a_good_database(self):
        Store(self.db).db.close()
        self.assertEqual(worker_mod.main(["--config", self.config, "--once"]), 0)


if __name__ == "__main__":
    unittest.main()
