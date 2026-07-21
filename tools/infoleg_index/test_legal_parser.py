import unittest

from legal_parser import extract_legal_changes, normalize_norm_number


class LegalParserTest(unittest.TestCase):
    def test_substituted_article_and_publication(self):
        text = "Art. 3° — Texto. (Artículo sustituido por art. 272 del Decreto N° 70/2023 B.O. 21/12/2023)"
        changes = extract_legal_changes(text)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].change_type, "sustituye")
        self.assertEqual(changes[0].affected_scope, "articulo")
        self.assertEqual(changes[0].amending_norm_number, "70/2023")
        self.assertEqual(changes[0].publication_date.isoformat(), "2023-12-21")

    def test_derogated_article_with_two_digit_year(self):
        text = "Art. 48.- (Artículo derogado por art. 154 de la Ley N° 24.013 B.O.17/12/91)"
        change = extract_legal_changes(text)[0]
        self.assertEqual(change.change_type, "deroga")
        self.assertEqual(change.publication_date.isoformat(), "1991-12-17")
        self.assertEqual(normalize_norm_number(change.amending_norm_number), "24013")

    def test_inciso_has_lower_confidence_scope(self):
        text = "a) Texto. (Inciso sustituido por art. 1° de la Ley N° 23890 B.O. 30/10/90)."
        change = extract_legal_changes(text)[0]
        self.assertEqual(change.affected_scope, "inciso")
        self.assertEqual(change.amending_article_reference, "1")

    def test_explicit_publication_based_effect_date(self):
        text = "(Artículo incorporado por art. 2 de la Ley N° 27.700 B.O. 1/2/2023. Vigencia: a partir de su publicación)"
        change = extract_legal_changes(text)[0]
        self.assertEqual(change.change_type, "incorpora")
        self.assertEqual(change.effect_date.isoformat(), "2023-02-01")

    def test_ignores_non_explicit_reference(self):
        self.assertEqual(extract_legal_changes("Ver Ley 20.744 y sus modificatorias."), [])


if __name__ == "__main__":
    unittest.main()
