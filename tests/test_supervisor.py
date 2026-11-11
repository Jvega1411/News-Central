import os
import sys
import time
import unittest

from newscentral import cli, config, supervisor
from newscentral.supervisor import Supervisor, next_delay


def quiet(*parts):
    pass


def wait_for(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class TestNextDelay(unittest.TestCase):
    def test_doubles_from_the_minimum_up_to_the_maximum(self):
        self.assertEqual(next_delay(0, 1, 10, 600, 600), 10)
        self.assertEqual(next_delay(10, 1, 10, 600, 600), 20)
        self.assertEqual(next_delay(320, 1, 10, 600, 600), 600)
        self.assertEqual(next_delay(600, 1, 10, 600, 600), 600)

    def test_resets_after_a_long_run(self):
        self.assertEqual(next_delay(320, 600, 10, 600, 600), 10)
        self.assertEqual(next_delay(320, 599, 10, 600, 600), 600)


class TestSupervisor(unittest.TestCase):
    def make(self, code):
        return Supervisor([sys.executable, "-c", code], min_delay=0.05, max_delay=0.2,
                          reset_after=60, log=quiet)

    def test_a_worker_that_exits_at_once_is_restarted_with_growing_delays(self):
        sup = self.make("import sys; sys.exit(1)")
        sup.start()
        try:
            self.assertTrue(wait_for(lambda: len(sup.delays) >= 4))
        finally:
            sup.stop()
        sup.join(5)
        self.assertEqual(sup.delays[:4], [0.05, 0.1, 0.2, 0.2])
        self.assertGreaterEqual(sup.starts, 4)
        self.assertFalse(sup.is_alive())

    def test_schema_mismatch_stops_the_restarts(self):
        sup = self.make("import sys; sys.exit(%d)" % supervisor.EXIT_SCHEMA)
        sup.start()
        sup.join(15)
        self.assertFalse(sup.is_alive())
        self.assertTrue(sup.gave_up)
        self.assertEqual(sup.starts, 1)

    def test_stop_terminates_a_running_worker(self):
        sup = self.make("import time; time.sleep(60)")
        sup.start()
        self.assertTrue(wait_for(lambda: sup.proc is not None))
        sup.stop()
        self.assertIsNotNone(sup.proc.poll())
        sup.join(5)
        self.assertFalse(sup.is_alive())
        self.assertEqual(sup.starts, 1)


class TestStartWorker(unittest.TestCase):
    def test_no_worker_starts_none(self):
        self.assertIsNone(cli.start_worker({"worker": False}, "config.json", start=False))

    def test_command_and_working_directory(self):
        sup = cli.start_worker({"worker": True}, "C:/x/config.json", start=False)
        self.assertEqual(sup.cmd, [sys.executable, "-m", "newscentral.worker", "--config", "C:/x/config.json",
                                   "--parent-pid", str(os.getpid())])
        self.assertEqual(sup.cwd, config.BASE_DIR)
        self.assertFalse(sup.is_alive())


if __name__ == "__main__":
    unittest.main()
