import json
import os
import shutil
import tempfile
import unittest

from newscentral import config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, obj, bom=False):
        with open(self.path, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
            json.dump(obj, f)

    def test_missing_file_gives_defaults(self):
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["port"], 8787)
        self.assertEqual(cfg["listen"], "127.0.0.1")
        self.assertEqual(cfg["read_langs"], ["es", "en"])
        self.assertEqual(cfg["refresh_minutes"], 15)
        self.assertIs(cfg["keep_awake"], False)
        self.assertIsNone(cfg["log_file"])

    def test_file_overrides_defaults(self):
        self.write({"port": 9000, "read_langs": ["es"]})
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(cfg["read_langs"], ["es"])
        self.assertEqual(cfg["refresh_minutes"], 15)

    def test_overrides_beat_file_and_none_is_ignored(self):
        self.write({"port": 9000})
        cfg = config.load_config(self.path, {"port": 9100, "listen": None})
        self.assertEqual(cfg["port"], 9100)
        self.assertEqual(cfg["listen"], "127.0.0.1")

    def test_file_with_bom_is_read(self):
        self.write({"port": 9000}, bom=True)
        self.assertEqual(config.load_config(self.path)["port"], 9000)

    def test_unknown_key_names_the_key(self):
        self.write({"prot": 9000})
        with self.assertRaises(config.ConfigError) as cm:
            config.load_config(self.path)
        self.assertIn("prot", str(cm.exception))

    def test_wrong_type_names_the_key(self):
        for bad in ({"port": "9000"}, {"port": True}, {"read_langs": "es"}, {"read_langs": [1]},
                    {"keep_awake": "yes"}, {"refresh_minutes": 0}):
            self.write(bad)
            with self.assertRaises(config.ConfigError) as cm:
                config.load_config(self.path)
            self.assertIn(list(bad)[0], str(cm.exception))

    def test_invalid_json_is_a_config_error(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(config.ConfigError):
            config.load_config(self.path)

    def test_paths_resolve_relative_to_repo(self):
        p = config.paths({"data_dir": "data", "log_file": "data/server.log"})
        self.assertEqual(p["db"], os.path.join(config.BASE_DIR, "data", "news.db"))
        self.assertEqual(p["status"], os.path.join(config.BASE_DIR, "data", "sources_status.json"))
        self.assertEqual(p["log"], os.path.join(config.BASE_DIR, "data", "server.log"))

    def test_absolute_data_dir_is_kept(self):
        p = config.paths({"data_dir": self.dir, "log_file": None})
        self.assertEqual(p["db"], os.path.join(self.dir, "news.db"))
        self.assertIsNone(p["log"])


if __name__ == "__main__":
    unittest.main()
