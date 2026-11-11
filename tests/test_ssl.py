import ssl
import unittest

from newscentral import http_client


class TestSslContext(unittest.TestCase):
    def test_still_verifies_chain_and_hostname(self):
        ctx = http_client.make_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    @unittest.skipUnless(hasattr(ssl, "VERIFY_X509_STRICT"), "flag not in this Python")
    def test_strict_x509_is_off(self):
        self.assertFalse(http_client.make_ssl_context().verify_flags & ssl.VERIFY_X509_STRICT)


if __name__ == "__main__":
    unittest.main()
