import unittest

from tests.helpers import ApiCase


class TestWorkerStatusApi(ApiCase):
    def test_status_lists_the_worker_jobs(self):
        self.store.set_worker_status("translate", "idle", None, 0, "", now=100.0)
        status, body = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertIs(body["worker_enabled"], True)
        self.assertEqual([(w["job"], w["state"]) for w in body["worker"]], [("translate", "idle")])

    def test_sources_and_items_carry_the_same_status(self):
        self.assertEqual(self.get("/api/sources")[1]["worker"], [])
        self.assertEqual(self.get("/api/items?since=0")[1]["worker"], [])


class TestWorkerDisabled(ApiCase):
    config = {"worker": False}

    def test_disabled_worker_is_reported(self):
        self.assertIs(self.get("/api/status")[1]["worker_enabled"], False)


if __name__ == "__main__":
    unittest.main()
