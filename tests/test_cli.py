import os
import shutil
import sys
import tempfile
import unittest

from newscentral import cli


class TestSetupLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "logs", "server.log")
        self.saved = (sys.stdout, sys.stderr)

    def tearDown(self):
        sys.stdout, sys.stderr = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_redirects_both_streams_and_creates_the_folder(self):
        stream = cli.setup_log(self.path)
        try:
            print("to stdout")
            print("to stderr", file=sys.stderr)
        finally:
            sys.stdout, sys.stderr = self.saved
            stream.close()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "to stdout\nto stderr\n")

    def test_rotates_a_large_file(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("x" * 200)
        stream = cli.setup_log(self.path, max_bytes=100)
        try:
            print("fresh")
        finally:
            sys.stdout, sys.stderr = self.saved
            stream.close()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "fresh\n")
        with open(self.path + ".1", encoding="utf-8") as f:
            self.assertEqual(len(f.read()), 200)

    def test_small_file_is_appended_to(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("old\n")
        stream = cli.setup_log(self.path)
        try:
            print("new")
        finally:
            sys.stdout, sys.stderr = self.saved
            stream.close()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old\nnew\n")


class TestKeepAwake(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_accepts_and_clears_the_request(self):
        self.assertTrue(cli.keep_awake(True))
        self.assertTrue(cli.keep_awake(False))

    @unittest.skipIf(os.name == "nt", "non-Windows only")
    def test_other_systems_ignore_it(self):
        self.assertFalse(cli.keep_awake(True))
        self.assertFalse(cli.keep_awake(False))


if __name__ == "__main__":
    unittest.main()
