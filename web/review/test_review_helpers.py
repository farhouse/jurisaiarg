from django.test import SimpleTestCase

from .views import normalized_article_label


class ArticleLabelTests(SimpleTestCase):
    def test_normalizes_ordinal_marks(self):
        self.assertEqual(normalized_article_label("1°"), "1")

    def test_preserves_bis_suffix(self):
        self.assertEqual(normalized_article_label("12 bis"), "12bis")

    def test_accepts_article_prefix_punctuation(self):
        self.assertEqual(normalized_article_label("48."), "48")
