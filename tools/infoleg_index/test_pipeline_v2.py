"""Acceptance cases: document integrity, contextual triage and links arriving in later batches."""
import os
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
from psycopg.types.json import Jsonb
from psycopg.conninfo import conninfo_to_dict

import worker
from crawl_infoleg_pg import add_version
from parser import parse_articles
from prepare_review import main as prepare
from references import sync_references
from enrich import classify_batch
from topics import classify_norm_topics
from review_documents import reparse_saved, refresh_queue, index_result
from text_integrity import COMPARISON_VERSION, INFOLEG_PAGE_TITLE

LAW_HTML='''<!doctype html><html><head><title>InfoLEG</title></head><body>
<h1>EXPROPIACIONES</h1><h2>Ley N° 23.600</h2>
<p>Declárase de utilidad pública y sujeto a expropiación un inmueble destinado al Poder Judicial de la Nación.</p>
<p>Sancionada: agosto 31 de 1988. Promulgada: setiembre 15 de 1988.</p>
<p>El Senado y Cámara de Diputados de la Nación Argentina reunidos en Congreso, etc., sancionan con fuerza de Ley:</p>
<p><b>Artículo 1° —</b> Declárase de utilidad pública y sujeto a expropiación con destino al Poder Judicial de la Nación, el inmueble ubicado en la calle Lavalle 1240/42/46/50 de esta Capital Federal, cuya nomenclatura catastral a continuación se detalla: sección 5, manzana 47, parcela 4, circunscripción 14. La finca se encuentra entre las calles Talcahuano y Libertad.</p>
<p><b>Art. 2° —</b> Facúltase a la Corte Suprema de Justicia de la Nación para determinar las dependencias judiciales que se instalarán en dicho inmueble.</p>
<p><b>Art. 3° —</b> Los gastos necesarios para su adquisición se imputarán a la cuenta prevista en el artículo 19 de la Ley 21.859 de “Tasas Judiciales”.</p>
<p><b>Art. 4° —</b> Comuníquese al Poder Ejecutivo. — JUAN C. PUGLIESE. — VICTOR H. MARTINEZ — Carlos A. Bravo. — Antonio J. Macris.</p>
<p>DADA EN LA SALA DE SESIONES DEL CONGRESO ARGENTINO, EN BUENOS AIRES, A LOS TREINTA Y UN DIAS DEL MES DE AGOSTO DEL AÑO MIL NOVECIENTOS OCHENTA Y OCHO.</p>
</body></html>'''


class ClearRouter:
    def __init__(self):self.seen=[]
    def predict(self,text,questions,**kwargs):
        self.seen.append(text)
        if 'change_type' in questions:
            return {'answers':{'change_type':{'choice':'sustituye','probabilities':{'sustituye':.98}}},'usage':{'truncated':False}}
        return {'answers':{key:{'choice':'si' if key=='m4' else 'no','probabilities':{'si':.98,'no':.98}} for key in questions},'usage':{'truncated':False}}


class ContextTest(unittest.TestCase):
    def test_full_context_and_formal_closure_are_not_separate_tasks(self):
        _,articles=parse_articles(LAW_HTML)
        router=ClearRouter()
        result=classify_norm_topics(router,'EXPROPIACIONES',articles)
        self.assertEqual([m['label'] for m in result['matters']],['Administrativo'])
        self.assertEqual(result['decision'],'aceptacion_automatica')
        self.assertEqual(result['articles'][-1]['scope'],'formal')
        self.assertTrue(all('EXPROPIACIONES' in text for text in router.seen))
        self.assertFalse(any(text.startswith('Comuníquese') for text in router.seen))
        self.assertIn('Poder Judicial',result['concepts'])
        self.assertIn('Utilidad pública',result['concepts'])


@unittest.skipUnless(os.getenv('CRAWLER_TEST_DSN'),'requires disposable database')
class PipelineV2Test(unittest.TestCase):
    def test_browser_title_recheck_preserves_the_snapshot_votes_and_draft(self):
        dsn=os.environ['CRAWLER_TEST_DSN']
        worker.initialize(dsn)
        with psycopg.connect(dsn) as con:
            norm=con.execute("INSERT INTO norms(infoleg_id,norm_type,number) VALUES (900202,'Ley','999002') RETURNING id").fetchone()[0]
            run=con.execute("INSERT INTO crawl_runs(source_id,requested_ids,status,finished_at) SELECT id,ARRAY[900202],'ok',now() FROM sources WHERE code='infoleg' RETURNING id").fetchone()[0]
            html=f'<head><title>{INFOLEG_PAGE_TITLE}</title></head><body><h1>EXPROPIACIONES</h1><p>Artículo 1° — Inmueble A.</p></body>'
            source=con.execute("INSERT INTO norm_sources(norm_id,crawl_run_id,url,document_kind,http_status,raw_content) VALUES (%s,%s,'https://servicios.infoleg.gob.ar/norma.htm','texto_original',200,%s) RETURNING id",(norm,run,html)).fetchone()[0]
            text,articles=parse_articles(html)
            legacy=INFOLEG_PAGE_TITLE+'\n'+text
            with con.cursor() as cur:add_version(cur,norm,source,'original',legacy,articles,False)
            result=index_result(con,run,norm)
            version=result['documents'][0]['norm_version_id']
            key=result['documents'][0]['integrity']['key']
            result['documents'][0]['integrity'].pop('comparison_version')
            result['documents'][0]['integrity'].update(status='diferencias',ignored_metadata=[],issues=['contenido_diferente'])
            result.update(decision='revision',review_reasons=['copia_diferente'])
            root=con.execute("INSERT INTO review_tasks(crawl_run_id,norm_id,kind,source_text,decision,fingerprint,model_result) VALUES (%s,%s,'indice',%s,'revision','header-snapshot',%s) RETURNING id",(run,norm,legacy,Jsonb(result))).fetchone()[0]
            checks={key:{'status':'validada','scope':'document','notes':'Fuente contrastada'}}
            con.execute("INSERT INTO norm_reviews(task_id,user_id,username,status,checks) VALUES (%s,900202,'header','validada',%s)",(root,Jsonb(checks)))
            con.execute("INSERT INTO review_events(task_id,username,previous_status,status,notes) VALUES (%s,'header','pendiente','validada','Fuente contrastada')",(root,))
            draft={'items':{'document':{'task_id':root,'fields':{'check':[key],'notes':['Anotación conservada']}}}}
            con.execute('INSERT INTO review_drafts(task_id,user_id,data,revision) VALUES (%s,900202,%s,3)',(root,Jsonb(draft)))
            # A later stored extraction must never be substituted for the reviewed snapshot.
            later_source=con.execute("INSERT INTO norm_sources(norm_id,crawl_run_id,url,document_kind,http_status,raw_content) VALUES (%s,%s,'https://servicios.infoleg.gob.ar/norma.htm','texto_original',200,%s) RETURNING id",(norm,run,html.replace('Inmueble A','Inmueble B'))).fetchone()[0]
            with con.cursor() as cur:add_version(cur,norm,later_source,'original',text.replace('Inmueble A','Inmueble B'),[{**articles[0],'text':articles[0]['text'].replace('Inmueble A','Inmueble B')}],False)
            con.commit()
            refresh_queue(con,run)
            updated,decision=con.execute('SELECT model_result,decision FROM review_tasks WHERE id=%s',(root,)).fetchone()
            document=updated['documents'][0]
            self.assertEqual((document['norm_version_id'],document['source_id'],document['text']),(version,source,legacy))
            self.assertEqual(document['integrity']['comparison_version'],COMPARISON_VERSION)
            self.assertEqual(document['integrity']['key'],key)
            self.assertEqual(document['integrity']['status'],'coincide')
            self.assertEqual(decision,'aceptacion_automatica')
            self.assertEqual(con.execute('SELECT checks FROM norm_reviews WHERE task_id=%s',(root,)).fetchone()[0],checks)
            self.assertEqual(con.execute('SELECT data,revision FROM review_drafts WHERE task_id=%s',(root,)).fetchone(),(draft,3))
            self.assertEqual(con.execute('SELECT count(*) FROM review_events WHERE task_id=%s',(root,)).fetchone()[0],1)
            with patch('review_documents.check_document',side_effect=AssertionError('proof already current')):
                refresh_queue(con,run)

    def test_legacy_history_and_saved_html_reprocessing(self):
        dsn=os.environ['CRAWLER_TEST_DSN']
        worker.initialize(dsn)
        with psycopg.connect(dsn) as con:
            norm=con.execute("INSERT INTO norms(infoleg_id,norm_type,number) VALUES (900201,'Ley','999001') RETURNING id").fetchone()[0]
            run=con.execute("INSERT INTO crawl_runs(source_id,requested_ids,status,finished_at) SELECT id,ARRAY[900201],'ok',now() FROM sources WHERE code='infoleg' RETURNING id").fetchone()[0]
            con.execute("INSERT INTO crawl_items(crawl_run_id,infoleg_id,norm_id,status) VALUES (%s,900201,%s,'solo_texto_original')",(run,norm))
            html='<h1>Ley 999001</h1><p>Artículo 1° — Texto íntegro.</p>'
            source=con.execute("INSERT INTO norm_sources(norm_id,crawl_run_id,url,document_kind,http_status,raw_content) VALUES (%s,%s,'https://servicios.infoleg.gob.ar/norma.htm','texto_original',200,%s) RETURNING id",(norm,run,html)).fetchone()[0]
            _,articles=parse_articles(html)
            oldtext=articles[0]['text']
            with con.cursor() as cur:add_version(cur,norm,source,'original',oldtext,articles,False)
            snapshot={'documents':[{'version':'original','text':oldtext}],'articles':[dict(a,version='original') for a in articles]}
            root=con.execute("INSERT INTO review_tasks(crawl_run_id,norm_id,kind,source_text,decision,fingerprint,model_result) VALUES (%s,%s,'indice',%s,'revision','legacy-root',%s) RETURNING id",(run,norm,oldtext,Jsonb(snapshot))).fetchone()[0]
            topic=con.execute("INSERT INTO review_tasks(crawl_run_id,norm_id,kind,source_text,decision,fingerprint,model_result,review_status,revision) VALUES (%s,%s,'pasaje',%s,'revision','legacy-article',%s,'validada',1) RETURNING id",(run,norm,oldtext,Jsonb({'category':'temas','matters':[{'label':'Civil y comercial','score':.9}],'concepts':[]}))).fetchone()[0]
            con.execute("INSERT INTO norm_reviews(task_id,user_id,username,status,checks) VALUES (%s,900201,'legacy','validada',%s)",(root,Jsonb({'identity':{'status':'validada'}})))
            con.execute("INSERT INTO review_events(task_id,username,previous_status,status,notes) VALUES (%s,'legacy','pendiente','validada','Historial')",(topic,))
            item={'task_id':root,'fields':{'check':['identity'],'notes':['No perder esta anotación']}}
            con.execute('INSERT INTO review_drafts(task_id,user_id,data) VALUES (%s,900201,%s)',(root,Jsonb({'items':{'identity':item}})))
            con.commit()
            refresh_queue(con,run)
            self.assertTrue(con.execute('SELECT superseded FROM review_tasks WHERE id=%s',(topic,)).fetchone()[0])
            draft=con.execute('SELECT data FROM review_drafts WHERE task_id=%s',(root,)).fetchone()[0]
            self.assertEqual(draft['items'],{})
            self.assertEqual(draft['archived_items']['identity'],item)
            self.assertEqual(con.execute('SELECT count(*) FROM review_events WHERE task_id=%s',(topic,)).fetchone()[0],1)
            con.commit()
            reparse_saved(con,run)
            prepare(['--dsn',dsn,'--run-id',str(run),'--indices-only','--refresh-documents'])
            result=con.execute('SELECT model_result FROM review_tasks WHERE id=%s',(root,)).fetchone()[0]
            self.assertEqual(result['documents'][0]['integrity']['status'],'coincide')
            self.assertTrue(result['document_history'])
            self.assertEqual(con.execute('SELECT count(*) FROM norm_versions WHERE norm_id=%s',(norm,)).fetchone()[0],2)
            self.assertEqual(con.execute('SELECT checks FROM norm_reviews WHERE task_id=%s',(root,)).fetchone()[0],{'identity':{'status':'validada'}})

    def test_simple_law_later_links_and_reference_resolution_preserve_reviews(self):
        dsn=os.environ['CRAWLER_TEST_DSN'];self.assertTrue(conninfo_to_dict(dsn)['dbname'].startswith('test_'))
        worker.initialize(dsn)
        with psycopg.connect(dsn) as con:
            def insert(infoleg,number,html):
                norm=con.execute("INSERT INTO norms(infoleg_id,norm_type,number,canonical_title) VALUES (%s,'Ley',%s,'EXPROPIACIONES') RETURNING id",(infoleg,number)).fetchone()[0]
                run=con.execute("INSERT INTO crawl_runs(source_id,requested_ids,status,finished_at) SELECT id,%s,'ok',now() FROM sources WHERE code='infoleg' RETURNING id",([infoleg],)).fetchone()[0]
                con.execute("INSERT INTO crawl_items(crawl_run_id,infoleg_id,norm_id,status) VALUES (%s,%s,%s,'solo_texto_original')",(run,infoleg,norm))
                source=con.execute("INSERT INTO norm_sources(norm_id,crawl_run_id,url,document_kind,http_status,raw_content) VALUES (%s,%s,'https://servicios.infoleg.gob.ar/norma.htm','texto_original',200,%s) RETURNING id",(norm,run,html)).fetchone()[0]
                text,articles=parse_articles(html)
                with con.cursor() as cur:add_version(cur,norm,source,'original',text,articles,False)
                con.commit();return norm,run
            norm,run=insert(900001,'23600',LAW_HTML)
            sync_references(con,[norm]);prepare(['--dsn',dsn,'--run-id',str(run),'--indices-only'])
            root,model=con.execute("SELECT id,model_result FROM review_tasks WHERE crawl_run_id=%s AND kind='indice'",(run,)).fetchone()
            proof=model['documents'][0]['integrity']
            self.assertEqual(proof['status'],'coincide')
            self.assertEqual(proof['article_count'],4)
            self.assertEqual(con.execute('SELECT decision FROM review_tasks WHERE id=%s',(root,)).fetchone()[0],'aceptacion_automatica')
            with patch.dict(sys.modules, {'laya':SimpleNamespace(Router=lambda **kwargs:ClearRouter())}):
                classify_batch(con,dsn,run,-1)
            self.assertEqual(con.execute("SELECT count(*) FROM review_tasks WHERE crawl_run_id=%s AND decision='revision' AND NOT superseded",(run,)).fetchone()[0],0)
            self.assertEqual(con.execute("SELECT count(*) FROM review_tasks WHERE crawl_run_id=%s AND model_result->>'scope'='norma' AND NOT superseded",(run,)).fetchone()[0],1)
            link=con.execute('SELECT resolution,relation_type,target_article FROM norm_references WHERE source_norm_id=%s',(norm,)).fetchone()
            self.assertEqual(link,('pendiente','cita','19'))
            checks={proof['key']:{'status':'validada','scope':'document','label':'Texto completo'}}
            con.execute("INSERT INTO norm_reviews(task_id,user_id,username,status,checks) VALUES (%s,900001,'acceptance','validada',%s)",(root,Jsonb(checks)));con.commit()
            other,second_run=insert(900102,'40000','<p>Artículo 1° — Sustitúyese el artículo 1 de la Ley 23.600 por el siguiente texto: inmueble destinado al Poder Judicial.</p>')
            sync_references(con,[other])
            self.assertEqual(con.execute('SELECT target_norm_id FROM norm_references WHERE source_norm_id=%s',(other,)).fetchone()[0],norm)
            self.assertEqual(con.execute('SELECT status,checks FROM norm_reviews WHERE task_id=%s',(root,)).fetchone(),('validada',checks))
            referenced,_=insert(900103,'21859','<p>Artículo 19° — Cuenta de tasas judiciales.</p>')
            sync_references(con,[referenced])
            self.assertEqual(con.execute('SELECT resolution,target_norm_id FROM norm_references WHERE source_norm_id=%s',(norm,)).fetchone(),('resuelta',referenced))
            con.commit()  # Release read locks before the idempotent DDL check on another connection.
            worker.initialize(dsn)
            self.assertEqual(con.execute('SELECT status,checks FROM norm_reviews WHERE task_id=%s',(root,)).fetchone(),('validada',checks))
