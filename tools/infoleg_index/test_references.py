import unittest
from references import extract_references


class ReferencesTest(unittest.TestCase):
    def test_citations_and_explicit_amendments_are_distinct(self):
        citation=list(extract_references('Art. 3° — Los gastos se imputarán a la cuenta prevista en el artículo 19 de la Ley 21.859 de Tasas Judiciales.'))
        self.assertEqual([(r['relation_type'],r['target_number'],r['target_article']) for r in citation],[('cita','21859','19')])
        change=list(extract_references('Artículo 1° — Sustitúyese el inciso b) del artículo 2 de la Ley N° 23.600 por el siguiente texto.'))
        self.assertEqual((change[0]['relation_type'],change[0]['scope'],change[0]['scope_detail']),('sustituye','inciso','b)'))
        self.assertEqual(list(extract_references('Art. 4° — Comuníquese al Poder Ejecutivo.')),[])
