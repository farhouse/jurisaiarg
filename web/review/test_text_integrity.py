from django.test import SimpleTestCase
from .text_integrity import check_document


class DocumentIntegrityTest(SimpleTestCase):
    def test_independent_source_detects_lost_text_structure_and_numbers(self):
        html='<html><head><title>No es contenido visible</title></head><body><h1>Ley 23600</h1><p><b>Artículo 1° —</b> Inmueble Lavalle 1240.</p><p>Contenido adicional.</p><p>Art. 2° — Comuníquese.</p></body></html>'
        text='Ley 23600\nArtículo 1° — Inmueble Lavalle 1240.\nContenido adicional.\nArt. 2° — Comuníquese.'
        articles=[{'label':'1','text':'Artículo 1° — Inmueble Lavalle 1240. Contenido adicional.'},{'label':'2','text':'Art. 2° — Comuníquese.'}]
        result=check_document(html,text,articles)
        self.assertEqual(result['status'],'coincide')
        self.assertNotEqual(result['key'],check_document(html,text.replace('1240','1241'),articles)['key'])
        self.assertEqual(check_document(html,text.replace('1240','1241'),articles)['status'],'diferencias')
        self.assertEqual(check_document(html,text,articles[:1])['status'],'diferencias')
        shortened=[{**articles[0],'text':'Artículo 1° — Inmueble Lavalle 1240.'},articles[1]]
        self.assertIn('contenido_del_articulo_diferente',check_document(html,text,shortened)['issues'])
        self.assertEqual(check_document(None,text,articles)['status'],'no_comprobable')
