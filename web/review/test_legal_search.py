from django.test import SimpleTestCase

from .legal_search import parse_legal_query


class LegalQueryParserTests(SimpleTestCase):
    def test_derogation_and_article(self):
        parsed = parse_legal_query("derogación artículo 48")
        self.assertEqual(parsed.action, "deroga")
        self.assertEqual(parsed.article, "48")
        self.assertEqual(parsed.text, "")

    def test_preserves_subject_for_full_text_search(self):
        parsed = parse_legal_query("derógase art. 48 contrato de trabajo")
        self.assertEqual(parsed.action, "deroga")
        self.assertEqual(parsed.article, "48")
        self.assertEqual(parsed.text, "contrato de trabajo")

    def test_understands_incorporation_variants(self):
        parsed = parse_legal_query("incorporación artículo 12 bis")
        self.assertEqual(parsed.action, "incorpora")
        self.assertEqual(parsed.article, "12 bis")
