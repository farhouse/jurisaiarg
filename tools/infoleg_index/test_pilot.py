import copy
import datetime
import json
import os
import unittest
import uuid
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pilot
from pilot_contract import DEFAULT_MODEL, VERSION, PROMPT_VERSION, validate_result

HTML = '''<html><head><title>InfoLEG - Ministerio de Justicia y Derechos Humanos - Argentina</title></head><body>
<h1>EXPROPIACIONES</h1><p>Ley 23600</p>
<p>Artículo 1° — Declárase de utilidad pública y sujeto a expropiación con destino al Poder Judicial de la Nación el inmueble ubicado en la calle Lavalle 1240.</p>
<p>Art. 2° — Facúltase a la Corte Suprema de Justicia de la Nación para determinar las dependencias judiciales que se instalarán en dicho inmueble.</p>
<p>Art. 3° — Los gastos necesarios para su adquisición se imputarán a la cuenta prevista en el artículo 19 de la Ley 21.859 de Tasas Judiciales.</p>
<p>Art. 4° — Comuníquese al Poder Ejecutivo.</p></body></html>'''


def fixture():
    row = (1,1,'Ley','23600','EXPROPIACIONES','Expropiación de un inmueble destinado al Poder Judicial.','Congreso','nacional')
    return pilot.snapshot_source(row,(1,'texto_original','https://example.invalid/norma',datetime.datetime.now(datetime.timezone.utc),HTML))


class PilotContractTest(unittest.TestCase):
    def test_law23600_evidence_coverage_and_citation(self):
        snapshot = fixture()
        result = pilot.reference_draft(snapshot)
        validate_result(result,snapshot)
        self.assertEqual(snapshot['integrity']['status'],'coincide')
        self.assertEqual(len(result['articles']),4)
        self.assertEqual(result['relations'][0]['type'],'cita')
        self.assertEqual(result['relations'][0]['target_number'],'21859')
        self.assertEqual(result['relations'][0]['target_article'],'19')
        self.assertNotIn('InfoLEG - Ministerio',snapshot['units'][0]['text'])
        image_snapshot=pilot.snapshot_source((1,1,'Ley','23600','EXPROPIACIONES','','Congreso','nacional'),
            (1,'texto_original','https://example.invalid/norma',datetime.datetime.now(datetime.timezone.utc),HTML.replace('</body>','<img src="anexo.png"></body>')))
        self.assertEqual(image_snapshot['resources_not_checked'][0]['url'],'https://example.invalid/anexo.png')
        resources=[{'kind':'img','url':url} for url in (
            'https://servicios.infoleg.gob.ar/infolegInternet/imagenes/left.png',
            'https://servicios.infoleg.gob.ar/infolegInternet/anexos/plano.png',
            'https://example.invalid/infolegInternet/imagenes/left.png')]
        self.assertEqual(pilot.analysis_resources(resources),resources[1:])
        for change in ('quote','foreign','missing','primary','direction'):
            bad = copy.deepcopy(result)
            if change=='quote': bad['summary']['evidence'][0]['quote']='Lavalle 1241'
            elif change=='foreign': bad['summary']['evidence'][0]['unit_id']='otra-norma'
            elif change=='missing': bad['articles'].pop()
            elif change=='primary': bad['matters'] += [{'label':'Penal','primary':True,'evidence':bad['summary']['evidence']}]
            else: bad['relations'][0]['source_unit_id']='article:0'
            with self.subTest(change=change),self.assertRaises(ValueError): validate_result(bad,snapshot)

    def test_preflight_and_inference_are_free_fixed_and_fail_closed(self):
        snapshot = fixture(); result = pilot.reference_draft(snapshot)
        good = {'provider_name':'test','context_length':1000000,'pricing':{'prompt':'0','completion':'0'},
                'supported_parameters':['structured_outputs','response_format']}
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'test-secret'}):
            for endpoints in ([],[{**good,'pricing':{'prompt':'1','completion':'0'}}],
                              [{**good,'supported_parameters':['response_format']}]):
                with patch('pilot.request_json',return_value=({'data':{'endpoints':endpoints}},{})),self.assertRaises(pilot.PilotError):
                    pilot.preflight(DEFAULT_MODEL)
            with patch('pilot.request_json',return_value=({'data':{'endpoints':[good]}},{})):
                self.assertEqual(pilot.preflight(DEFAULT_MODEL)['context_length'],1000000)
            with self.assertRaises(pilot.PilotError): pilot.preflight('modelo-pago')
            response = {'model':DEFAULT_MODEL.removesuffix(':free'),'provider':'test','id':'test-response',
                'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],
                'usage':{'total_tokens':100,'cost':0}}
            with patch('pilot.request_json',return_value=(response,{'X-RateLimit-Remaining-Tokens':'900'})) as api:
                value,meta = pilot.infer(snapshot,{},DEFAULT_MODEL,1000000)
                self.assertEqual(value,result); self.assertEqual(meta['remaining_tokens'],'900')
                payload = api.call_args.args[2]
                self.assertEqual(payload['provider']['max_price'],{'prompt':0,'completion':0})
                self.assertTrue(payload['provider']['require_parameters'])
                self.assertTrue(payload['response_format']['json_schema']['strict'])
                self.assertEqual(payload['max_tokens'],32768)
                self.assertEqual(payload['reasoning'],{'max_tokens':4096})
                self.assertEqual(api.call_args.kwargs['timeout'],360)
                self.assertNotIn('models',payload)
            for response_bad in ({**response,'model':'different-model'},
                {**response,'usage':{'total_tokens':100,'cost':0.01}},
                {**response,'choices':[{'finish_reason':'length'}]},
                {**response,'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}):
                with patch('pilot.request_json',return_value=(response_bad,{})),self.assertRaises(pilot.PilotError):
                    pilot.infer(snapshot,{},DEFAULT_MODEL,1000000,{'max_tokens':8192})
            with patch('pilot.request_json') as api,self.assertRaises(pilot.PilotError):
                pilot.infer(snapshot,{},DEFAULT_MODEL,10)
            api.assert_not_called()
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':''}),self.assertRaises(pilot.PilotError): pilot.preflight(DEFAULT_MODEL)
        with patch('pilot.urlopen',side_effect=HTTPError('url',401,'test-secret',{},None)):
            with self.assertRaises(pilot.PilotError) as caught: pilot.request_json('test','test-secret')
            self.assertNotIn('test-secret',str(caught.exception))

    def test_incomplete_responses_keep_safe_diagnostics_and_distinguish_causes(self):
        snapshot=fixture();result=pilot.reference_draft(snapshot)
        response={'model':DEFAULT_MODEL,'provider':'test','id':'gen-test',
            'usage':{'completion_tokens':8192,'completion_tokens_details':{'reasoning_tokens':8192}},
            'choices':[{'finish_reason':'length','native_finish_reason':'max_tokens','message':{'content':''}}]}
        cases=[(response,'límite de salida de 8192'),
            ({**response,'choices':[{'finish_reason':'content_filter','message':{'refusal':'test-secret'}}]},'rechazó'),
            ({**response,'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result),'refusal':'test-secret'}}]},'rechazó'),
            ({**response,'choices':[{'finish_reason':'stop','message':{'content':''}}]},'sin devolver la ficha'),
            ({**response,'choices':None,'error':{'code':429,'message':'test-secret'}},'cupo o límite temporal'),
            ({**response,'choices':[{'finish_reason':'error','error':{'code':502,'message':'test-secret',
                'metadata':{'error_type':'provider_unavailable'}}}]},'falló durante la generación'),
            ({**response,'choices':{},'error':{'code':400,'metadata':{'error_type':'context_length_exceeded'}}},'análisis por partes'),
            ({**response,'choices':[{'finish_reason':'error','native_finish_reason':'test-secret'}]},'finalización normal')]
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'test-secret'}):
            for raw,expected in cases:
                with self.subTest(cause=expected),patch('pilot.request_json',return_value=(raw,{})),self.assertRaises(pilot.PilotError) as caught:
                    pilot.infer(snapshot,{},DEFAULT_MODEL,1000000,{'max_tokens':8192})
                self.assertIn(expected,str(caught.exception))
                meta=caught.exception.metadata
                self.assertEqual(meta['request_id'],'gen-test')
                self.assertEqual(meta['max_tokens'],8192)
                self.assertEqual(meta['usage']['reasoning_tokens'],8192)
                self.assertNotIn('test-secret',str(caught.exception)+json.dumps(meta))
                self.assertNotIn('content',meta)

    def test_generation_budgets_are_validated_and_frozen_after_capability_check(self):
        endpoint={'context_length':1000000,'max_completion_tokens':65536,'pricing':{'prompt':'0','completion':'0'},
            'supported_parameters':['structured_outputs','response_format','reasoning']}
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'test-secret','JURISAI_PILOT_MAX_TOKENS':'32768','JURISAI_PILOT_REASONING_TOKENS':'4096'}):
            with patch('pilot.request_json',return_value=({'data':{'endpoints':[endpoint]}},{})):
                self.assertEqual(pilot.preflight(DEFAULT_MODEL)['request_settings'],{'max_tokens':32768,'reasoning':{'max_tokens':4096}})
            without_reasoning={**endpoint,'supported_parameters':['structured_outputs','response_format']}
            with patch('pilot.request_json',return_value=({'data':{'endpoints':[without_reasoning]}},{})):
                self.assertEqual(pilot.preflight(DEFAULT_MODEL)['request_settings'],{'max_tokens':32768})
            with patch('pilot.request_json',return_value=({'data':{'endpoints':[{**endpoint,'max_completion_tokens':16000}]}},{})),self.assertRaises(pilot.PilotError):
                pilot.preflight(DEFAULT_MODEL)
            for output,reasoning in [('texto','4096'),('32768','0'),('4096','4096'),('200000','4096')]:
                with self.subTest(output=output,reasoning=reasoning),patch.dict(os.environ,{'JURISAI_PILOT_MAX_TOKENS':output,'JURISAI_PILOT_REASONING_TOKENS':reasoning}),self.assertRaises(pilot.PilotError):
                    pilot.generation_settings()


@unittest.skipUnless(os.getenv('CRAWLER_TEST_DSN'),'requiere PostgreSQL test_ aislado')
class PilotIntegrationTest(unittest.TestCase):
    def test_snapshots_reuse_and_recovery_never_touch_existing_reviews(self):
        import worker
        from psycopg.conninfo import conninfo_to_dict
        dsn = os.environ['CRAWLER_TEST_DSN']
        self.assertTrue(conninfo_to_dict(dsn)['dbname'].startswith('test_'))
        worker.initialize(dsn)
        with psycopg.connect(dsn) as con:
            run = con.execute("INSERT INTO crawl_runs(source_id,status,finished_at,requested_ids) SELECT id,'ok',now(),'{170001}' FROM sources WHERE code='infoleg' RETURNING id").fetchone()[0]
            norm = con.execute("INSERT INTO norms(infoleg_id,norm_type,number,canonical_title,summary) VALUES(170001,'Ley','23600','EXPROPIACIONES','Expropiación de un inmueble.') RETURNING id").fetchone()[0]
            con.execute("INSERT INTO crawl_items(crawl_run_id,infoleg_id,norm_id,status) VALUES(%s,170001,%s,'completa')",(run,norm))
            con.execute("INSERT INTO norm_sources(norm_id,crawl_run_id,url,document_kind,http_status,raw_content) VALUES(%s,%s,'https://example.invalid','texto_original',200,%s)",(norm,run,HTML))
            task = con.execute("INSERT INTO review_tasks(crawl_run_id,norm_id,kind,source_text,decision,fingerprint) VALUES(%s,%s,'indice','Original','revision',%s) RETURNING id",(run,norm,uuid.uuid4().hex)).fetchone()[0]
            con.execute("INSERT INTO norm_reviews(task_id,user_id,username,status) VALUES(%s,1,'original','validada')",(task,))
            con.execute("INSERT INTO review_drafts(task_id,user_id,data,revision) VALUES(%s,1,'{}',7)",(task,))
            job = con.execute("INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by,mode,crawl_run_id) VALUES(%s,170001,170001,'test','pilot',%s) RETURNING id",(uuid.uuid4(),run)).fetchone()[0]
            pk = con.execute("INSERT INTO analysis_pilots(job_id,crawl_run_id,model,config) VALUES(%s,%s,%s,%s) RETURNING id",(job,run,DEFAULT_MODEL,Jsonb({'contract':VERSION,'prompt':PROMPT_VERSION}))).fetchone()[0]
            con.commit()
            def originals():
                return {table:con.execute(sql.SQL("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id),'[]') FROM {} t").format(sql.Identifier(table))).fetchone()[0]
                    for table in ('norms','norm_sources','norm_versions','article_versions','legal_changes','norm_references','crawl_runs','review_tasks','norm_reviews','review_drafts','review_events')}
            before = originals()
            self.assertTrue(worker.process_pilot(con,dsn,{'id':job}))
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s',(job,)).fetchone()[0],'done')
            item,snapshot,reference,key = con.execute('SELECT id,snapshot,reference,fingerprint FROM analysis_pilot_items WHERE pilot_id=%s',(pk,)).fetchone()
            validate_result(reference,snapshot)
            meta = {'request_id':'one','requested':True,'usage':{'total_tokens':100,'cost':0},'seconds':1}
            con.execute("UPDATE analysis_pilots SET config=config || %s WHERE id=%s",(Jsonb({'endpoint':{'context_length':1000000}}),pk))
            con.execute("UPDATE analysis_pilot_items SET metadata=%s WHERE id=%s",(Jsonb({'reference_authors':{'summary':'first'},'reference_decisions':{'summary':'confirmed'}}),item))
            con.commit()
            failed={'requested':True,'finish_reason':'length','usage':{'completion_tokens':8192,'reasoning_tokens':8192}}
            with patch('pilot.infer',side_effect=pilot.PilotError('Límite de salida.',failed)),self.assertRaises(pilot.PilotError):
                pilot.analyze(con,pk,job,lambda *a,**k:None)
            state=con.execute('SELECT status,result,reference,metadata FROM analysis_pilot_items WHERE id=%s',(item,)).fetchone()
            self.assertEqual(state[0],'error');self.assertFalse(state[1]);self.assertEqual(state[2],reference)
            self.assertEqual(state[3]['attempts'],[failed])
            with patch('pilot.infer',return_value=(reference,meta)) as infer:
                pilot.analyze(con,pk,job,lambda *a,**k:None)
                self.assertEqual(infer.call_count,1)
                pilot.analyze(con,pk,job,lambda *a,**k:None)
                self.assertEqual(infer.call_count,1)
            self.assertEqual(len(con.execute('SELECT metadata FROM analysis_pilot_items WHERE id=%s',(item,)).fetchone()[0]['attempts']),2)
            job2 = con.execute("INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by,mode,crawl_run_id) VALUES(%s,170001,170001,'test','pilot',%s) RETURNING id",(uuid.uuid4(),run)).fetchone()[0]
            pk2 = con.execute("INSERT INTO analysis_pilots(job_id,crawl_run_id,model,config) SELECT %s,crawl_run_id,model,config FROM analysis_pilots WHERE id=%s RETURNING id",(job2,pk)).fetchone()[0]
            con.execute("INSERT INTO analysis_pilot_items(pilot_id,norm_id,snapshot,reference,split,fingerprint,metadata) SELECT %s,norm_id,snapshot,reference,split,fingerprint,%s FROM analysis_pilot_items WHERE id=%s",(pk2,Jsonb({'reference_authors':{'summary':'second'},'reference_decisions':{'summary':'legal'}}),item))
            con.commit()
            with patch('pilot.infer',side_effect=AssertionError('debe reutilizar')): pilot.analyze(con,pk2,job2,lambda *a,**k:None)
            metadata = con.execute('SELECT metadata FROM analysis_pilot_items WHERE pilot_id=%s',(pk2,)).fetchone()[0]
            self.assertTrue(metadata['reused'])
            self.assertEqual(metadata['reference_authors']['summary'],'second')
            self.assertEqual(metadata['reference_decisions']['summary'],'legal')
            self.assertEqual(metadata['attempts'],[])
            # Otro presupuesto no reutiliza una respuesta producida con ajustes distintos.
            con.execute("UPDATE analysis_pilot_items SET status='pending',result='{}' WHERE pilot_id=%s",(pk2,))
            con.execute("UPDATE analysis_pilots SET config=jsonb_set(config,'{endpoint,request_settings}',%s) WHERE id=%s",
                (Jsonb({'max_tokens':49152,'reasoning':{'max_tokens':4096}}),pk2))
            con.commit()
            with patch('pilot.infer',return_value=(reference,meta)) as infer:
                pilot.analyze(con,pk2,job2,lambda *a,**k:None)
                self.assertEqual(infer.call_count,1)
                self.assertEqual(infer.call_args.args[4]['max_tokens'],49152)
            # El modo directo congela la muestra y continúa sin ninguna evaluación humana.
            con.execute("UPDATE analysis_pilots SET phase='prepare',config=config || %s WHERE id=%s",(Jsonb({'reference_required':False}),pk2))
            con.commit()
            real_popen=worker.subprocess.Popen
            stages=[]
            def child(args, **kwargs):
                stages.append(args[2])
                if args[2]=='prepare':return real_popen(args,**kwargs)
                return Mock(wait=Mock(return_value=0),poll=Mock(return_value=0))
            with patch('worker.subprocess.Popen',side_effect=child):
                self.assertTrue(worker.process_pilot(con,dsn,{'id':job2}))
            self.assertEqual(stages,['prepare','preflight','laya','analyze'])
            self.assertEqual(con.execute('SELECT phase FROM analysis_pilots WHERE id=%s',(pk2,)).fetchone()[0],'analyze')
            self.assertEqual(con.execute('SELECT count(*) FROM analysis_pilot_evaluations WHERE item_id=%s',(item,)).fetchone()[0],0)
            # Ni siquiera la recuperación de un piloto interrumpido cambia su lote de origen.
            con.execute("UPDATE crawl_jobs SET status='running' WHERE id=%s",(job2,));con.commit()
            worker.recover_interrupted(con)
            self.assertEqual(originals(),before)
