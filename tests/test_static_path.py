# tests/test_static_path.py
import os, unittest
from newscentral import api, config

STATIC = os.path.join(config.BASE_DIR, "static")

class TestStaticPath(unittest.TestCase):
    def test_normal_paths(self):
        self.assertEqual(api.static_path("/js/map.js"), os.path.join(STATIC, "js", "map.js"))
        self.assertEqual(api.static_path("/world.svg"), os.path.join(STATIC, "world.svg"))

    def test_rejects_traversal(self):
        for bad in ("/js/../../server.js", "/js/..\..\server.js", "/js/..%5c", "/..", "/js/c:/x.js",
                    "/js/..\../x.js", ""):
            got = api.static_path(bad)
            if got is not None:  # anything accepted must stay inside static/
                self.assertTrue(os.path.realpath(got).startswith(os.path.realpath(STATIC) + os.sep), bad)
        for bad in ("/js/../../server.js", "/js/..\..\server.js", "/..", "/js/c:/x.js", ""):
            self.assertIsNone(api.static_path(bad), bad)

if __name__ == "__main__":
    unittest.main()
