import unittest

from crawl_infoleg_pg import decode_html


class DecodeHtmlTest(unittest.TestCase):
    def test_removes_nul_from_text_representation(self):
        raw = b"<p>Ley\x00 23706</p>"
        decoded = decode_html(raw, "windows-1252")
        self.assertEqual(decoded, "<p>Ley 23706</p>")
        self.assertIn(b"\x00", raw)

    def test_falls_back_for_unknown_charset(self):
        decoded = decode_html("Información".encode("windows-1252"), "unknown-charset")
        self.assertEqual(decoded, "Información")


if __name__ == "__main__":
    unittest.main()
