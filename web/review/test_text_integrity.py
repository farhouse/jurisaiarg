from django.test import SimpleTestCase
from .text_integrity import INFOLEG_PAGE_TITLE, check_document


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

    def test_legacy_browser_title_is_not_a_legal_difference(self):
        text='EXPROPIACIONES\nArtículo 1° — Inmueble Lavalle 1240.'
        html=f'<html><head><title>{INFOLEG_PAGE_TITLE}</title></head><body><h1>EXPROPIACIONES</h1><p>Artículo 1° — Inmueble Lavalle 1240.</p></body></html>'
        articles=[{'label':'1','text':text.split('\n')[1]}]
        legacy=INFOLEG_PAGE_TITLE+'\n'+text
        result=check_document(html,legacy,articles)
        self.assertEqual(result['status'],'coincide')
        self.assertEqual(result['differences'],[])
        self.assertEqual(result['ignored_metadata'],[{'kind':'html_title','text':INFOLEG_PAGE_TITLE}])
        self.assertNotEqual(result['extracted_hash'],result['source_hash'])  # Raw evidence remains identifiable.
        self.assertEqual(check_document(html,legacy.replace('1240','1241'),articles)['status'],'diferencias')
        # A generic title is never treated as discardable metadata.
        self.assertEqual(check_document(html.replace(INFOLEG_PAGE_TITLE,'EXPROPIACIONES'),'EXPROPIACIONES\n'+text,articles)['status'],'diferencias')
        self.assertEqual(check_document(html.replace('<title>'+INFOLEG_PAGE_TITLE+'</title>',''),legacy,articles)['status'],'diferencias')
        # The same words in a legal quotation must remain part of the comparison.
        quoted='Artículo 1° — La denominación es '+INFOLEG_PAGE_TITLE+'.'
        quoted_html=f'<head><title>{INFOLEG_PAGE_TITLE}</title></head><body><p>{quoted}</p></body>'
        self.assertEqual(check_document(quoted_html,quoted,[{'label':'1','text':quoted}])['ignored_metadata'],[])
        self.assertEqual(check_document(quoted_html,'Artículo 1° — La denominación es Argentina.',[{'label':'1','text':quoted}])['status'],'diferencias')
