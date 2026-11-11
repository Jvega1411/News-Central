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

    def test_worker_defaults(self):
        cfg = config.load_config(self.path)
        self.assertIs(cfg["worker"], True)
        self.assertEqual(cfg["translate_batch"], 16)
        self.assertIsNone(cfg["worker_threads"])
        self.assertEqual(cfg["translate_engine"], {"default": "nllb-600m"})
        self.assertEqual(config.TARGET_LANG, "es")

    def test_worker_values_from_file(self):
        self.write({"worker": False, "translate_batch": 8, "worker_threads": 2,
                    "translate_engine": {"default": "opus-mt", "zh": "nllb-600m"}})
        cfg = config.load_config(self.path)
        self.assertIs(cfg["worker"], False)
        self.assertEqual(cfg["translate_batch"], 8)
        self.assertEqual(config.worker_threads(cfg), 2)
        self.assertEqual(cfg["translate_engine"]["zh"], "nllb-600m")

    def test_worker_override_from_flag(self):
        cfg = config.load_config(self.path, {"worker": False})
        self.assertIs(cfg["worker"], False)

    def test_bad_worker_values_name_the_key(self):
        for bad in ({"worker": "no"}, {"translate_batch": 0}, {"translate_batch": 1000},
                    {"worker_threads": 0}, {"worker_threads": "2"}, {"translate_engine": "nllb-600m"},
                    {"translate_engine": {"zh": "opus-mt"}}, {"translate_engine": {"default": ""}},
                    {"translate_engine": {"default": "nllb-600m", "zh": 3}}):
            self.write(bad)
            with self.assertRaises(config.ConfigError) as cm:
                config.load_config(self.path)
            self.assertIn(list(bad)[0], str(cm.exception))

    def test_worker_threads_default_is_half_the_cores(self):
        cores = os.cpu_count() or 2
        self.assertEqual(config.worker_threads({"worker_threads": None}), max(1, cores // 2))

    def test_paths_for_models_and_bench(self):
        p = config.paths({"data_dir": self.dir, "log_file": None})
        self.assertEqual(p["models"], os.path.join(self.dir, "models"))
        self.assertEqual(p["bench"], os.path.join(self.dir, "bench"))


if __name__ == "__main__":
    unittest.main()
