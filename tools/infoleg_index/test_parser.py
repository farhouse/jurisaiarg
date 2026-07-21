import unittest

from parser import parse_articles, parse_card, parse_relation_rows


class ParserTest(unittest.TestCase):
    def test_card(self):
        html = """
        <div id="Textos_Completos">
          <strong>Ley 20744 HONORABLE CONGRESO DE LA NACION ARGENTINA</strong>
          <span class="vr_azul11">05-sep-1974</span>
          <span class="destacado">LEY DE CONTRATO DE TRABAJO</span>
          <h1>REGIMEN</h1>
          <p>Publicada en el Boletín Oficial del 27-sep-1974 Número: 23003</p>
          <p><strong>Resumen:</strong> REGIMEN DEL CONTRATO DE TRABAJO.</p>
          <a href="anexos/25552/norma.htm">Texto completo de la norma</a>
          <a href="anexos/25552/texact.htm">Texto actualizado de la norma</a>
        </div>"""
        card = parse_card(html, "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=25552")
        self.assertEqual(card.norm_type, "Ley")
        self.assertEqual(card.number, "20744")
        self.assertEqual(card.sanction_date.isoformat(), "1974-09-05")
        self.assertEqual(card.publication_date.isoformat(), "1974-09-27")
        self.assertTrue(card.current_url.endswith("anexos/25552/texact.htm"))

    def test_empty_card_uses_nulls(self):
        card = parse_card("<div id='Textos_Completos'><span class='destacado'> </span><h1> </h1></div>",
                          "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=5")
        self.assertIsNone(card.canonical_title)
        self.assertIsNone(card.subject)
        self.assertIsNone(card.summary)

    def test_articles(self):
        html = """<p>Artículo 1º — Primero.</p><p>Texto uno.</p>
        <p>Art. 2° — Segundo.</p><p>Texto dos.</p>
        <p>Antecedentes Normativos</p><p>Artículo 1º — Texto histórico.</p>"""
        _, articles = parse_articles(html)
        self.assertEqual([a["key"] for a in articles], ["1", "2"])
        self.assertIn("Texto uno", articles[0]["text"])

    def test_relations(self):
        html = """
        <table><tr><td><a href="/infolegInternet/verNorma.do?id=77206">Ley 21659</a>
        PODER EJECUTIVO NACIONAL</td><td>12-oct-1977</td>
        <td>CONTRATO DE TRABAJO<br>LEY 20744 - MODIFICACION</td></tr></table>"""
        rows = parse_relation_rows(html)
        self.assertEqual(rows[0]["infoleg_id"], 77206)
        self.assertEqual(rows[0]["number"], "21659")


if __name__ == "__main__":
    unittest.main()
