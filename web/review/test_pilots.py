import uuid
import json
from unittest.mock import patch
from urllib.error import URLError
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase,Client,SimpleTestCase
from django.utils import timezone
from .models import Norm,CrawlRun,CrawlItem,CrawlJob,AnalysisPilot,AnalysisPilotItem,AnalysisPilotEvaluation,ReviewCard,CardResponse
from .pilot_contract import empty_result,DEFAULT_MODEL,VERSION,PROMPT_VERSION,request_budget
from .pilots import model_choices,ModelForm

LIMITS={'max_tokens':32768,'reasoning_tokens':4096,'stages':['laya','llm']}


class ModelCatalogTest(SimpleTestCase):
    def setUp(self):
        cache.delete('jurisai.pilot-models.v1')
        self.addCleanup(cache.delete,'jurisai.pilot-models.v1')

    def test_token_limits_validate_integer_bounds_and_reserve_json_output(self):
        valid={'model':DEFAULT_MODEL,'submission_id':uuid.uuid4(),**LIMITS}
        with patch('review.pilots.model_choices',return_value=([(DEFAULT_MODEL,'Nemotron')],'')):
            form=ModelForm(valid)
            self.assertTrue(form.is_valid())
            self.assertEqual(form.cleaned_data['request_settings'],request_budget(32768,4096))
            for changes in ({'max_tokens':'texto'},{'max_tokens':-1},{'max_tokens':131073},
                    {'reasoning_tokens':0},{'reasoning_tokens':32768},{'max_tokens':4096,'reasoning_tokens':4096}):
                with self.subTest(changes=changes):
                    form=ModelForm({**valid,**changes});self.assertFalse(form.is_valid())
                    self.assertTrue('max_tokens' in form.errors or 'reasoning_tokens' in form.errors)

    def test_stage_checkboxes_require_a_choice_and_laya_needs_no_api_configuration(self):
        data={'submission_id':uuid.uuid4(),'stages':['laya']}
        with patch('review.pilots.model_choices',side_effect=AssertionError('Laya no consulta OpenRouter')):
            form=ModelForm(data)
            self.assertTrue(form.is_valid(),form.errors)
            self.assertEqual(form.cleaned_data['stages'],['laya'])
        with patch('review.pilots.model_choices',return_value=([(DEFAULT_MODEL,'Nemotron')],'')):
            for stages in ([],['invalid']):
                form=ModelForm({**LIMITS,'model':DEFAULT_MODEL,'submission_id':uuid.uuid4(),'stages':stages})
                self.assertFalse(form.is_valid());self.assertIn('stages',form.errors)

    def test_public_catalog_only_offers_free_structured_models_and_is_cached(self):
        good={'id':'test/compatible:free','name':'Compatible','pricing':{'prompt':'0','completion':'0'},
            'supported_parameters':['response_format','structured_outputs']}
        entries=[good,{**good,'id':'test/paid','pricing':{'prompt':'1','completion':'1'}},
            {**good,'id':'test/not-free:free','pricing':{'prompt':'0','completion':'1'}},
            {**good,'id':'test/no-schema:free','supported_parameters':['response_format']}]
        with patch('review.pilots.urlopen') as api:
            api.return_value.__enter__.return_value.read.return_value=json.dumps({'data':entries}).encode()
            self.assertEqual(model_choices(),([('test/compatible:free','Compatible')],''))
            self.assertEqual(model_choices()[0],[('test/compatible:free','Compatible')])
            api.assert_called_once_with('https://openrouter.ai/api/v1/models',timeout=5)

    def test_catalog_outage_has_explicit_fallback_and_empty_catalog_has_no_choices(self):
        with patch('review.pilots.urlopen',side_effect=URLError('provider-private-details')) as api:
            choices,warning=model_choices()
            self.assertEqual(choices[0][0],DEFAULT_MODEL)
            self.assertIn('No se pudo consultar',warning)
            self.assertNotIn('provider-private-details',warning)
            model_choices();self.assertEqual(api.call_count,1)
        cache.delete('jurisai.pilot-models.v1')
        with patch('review.pilots.urlopen') as api:
            api.return_value.__enter__.return_value.read.return_value=b'{"data":[]}'
            choices,warning=model_choices()
            self.assertFalse(choices);self.assertIn('no anuncia',warning)


class PilotsTest(TransactionTestCase):
    tables = [Norm,CrawlRun,CrawlItem,CrawlJob,AnalysisPilot,AnalysisPilotItem,AnalysisPilotEvaluation,ReviewCard,CardResponse]

    def setUp(self):
        catalog=patch('review.pilots.model_choices',return_value=([(DEFAULT_MODEL,'Nemotron'),('test/alternate:free','Modelo alternativo')],''))
        catalog.start();self.addCleanup(catalog.stop)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema:
            for model in cls.tables: schema.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            for model in reversed(cls.tables): schema.delete_model(model)
        super().tearDownClass()

    def tearDown(self):
        # Django no vacía automáticamente las tablas jurídicas managed=False.
        for model in reversed(self.tables):
            model.objects.all().delete()
        super().tearDown()

    def test_pilot_permissions_reference_lock_revisions_and_source(self):
        admin = User.objects.create_user('pilot-admin',is_staff=True)
        other = User.objects.create_user('pilot-second',is_staff=True)
        validator = User.objects.create_user('pilot-validator')
        now = timezone.now()
        norm = Norm.objects.create(infoleg_id=1,norm_type='Ley',number='23600',canonical_title='EXPROPIACIONES',general_status='desconocida',index_status='solo_texto_original',article_count=1)
        run = CrawlRun.objects.create(requested_ids=[1],started_at=now,finished_at=now,status='ok',processed_count=1)
        self.assertEqual(self.client.get('/gestion/pilotos/').status_code,302)
        self.client.force_login(validator)
        self.assertEqual(self.client.get('/gestion/pilotos/').status_code,403)
        self.client.force_login(admin)
        response = self.client.post('/gestion/pilotos/',{'run':run.pk,'model':DEFAULT_MODEL,'submission_id':uuid.uuid4(),'reference_required':'on',**LIMITS})
        self.assertEqual(response.status_code,302)
        pilot = AnalysisPilot.objects.get()
        job = pilot.job;job.status='done';job.save()
        pilot.status='reference';pilot.save()
        snapshot = {'identity':{'type':'Ley','number':'23600','infoleg_id':1,'title':'EXPROPIACIONES','issuing_body':'Congreso','jurisdiction':'nacional'},
            'source':{'html':'<p>Fuente</p><script>unsafe()</script>','version':'original','retrieved_at':now.isoformat()},
            'integrity':{'status':'coincide'},'units':[{'id':'document','kind':'document','label':'Texto completo','text':'Declárase de utilidad pública. Conforme al artículo 19 de la Ley 21.859.'}],'candidates':[]}
        result=empty_result();result['summary']={'text':'Declara utilidad pública.','evidence':[{'unit_id':'document','quote':'Declárase de utilidad pública.'}]}
        result['articles']=[{'unit_id':'document','function':'Declara utilidad pública.'}]
        result['relations']=[{'source_unit_id':'document','direction':'outgoing','target_type':'Ley','target_number':'21859','target_article':'19',
            'type':'cita','scope':'articulo','detail':'','evidence':[{'unit_id':'document','quote':'Conforme al artículo 19 de la Ley 21.859.'}]}]
        item=AnalysisPilotItem.objects.create(pilot=pilot,norm=norm,snapshot=snapshot,reference=result,split='holdout',fingerprint='fixture')
        url=f'/gestion/pilotos/{pilot.pk}/normas/{item.pk}/'
        endpoint=url+'evaluar/'
        self.assertContains(self.client.get(url),'La ficha todavía no fue generada')
        self.assertContains(self.client.get(url+'?reference=1'),'Referencia manual · evaluación avanzada')
        self.assertContains(self.client.get(url+'?reference=1'),'Esperando incorporación de la norma referida')
        self.assertEqual(self.client.post(response.url,{'action':'analyze'}).status_code,400)
        payload={'phase':'reference','block':'summary','summary-revision':0,'summary-decision':'confirmed'}
        self.assertEqual(self.client.post(endpoint,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code,200)
        self.assertEqual(self.client.post(endpoint,payload).status_code,409)
        # Otra persona no reemplaza la revisión previa.
        self.client.force_login(other)
        self.assertEqual(self.client.post(endpoint,payload).status_code,302)
        self.assertEqual(item.evaluations.filter(block='summary').count(),2)
        self.client.force_login(admin)
        for block in ('matters','concepts','articles','relations'):
            data={'phase':'reference','block':block,f'{block}-revision':0,f'{block}-decision':'confirmed'}
            if block=='relations':data['relations-relation_count']=1
            self.assertEqual(self.client.post(endpoint,data).status_code,302)
        self.assertEqual(self.client.post(response.url,{'action':'analyze'}).status_code,302)
        pilot.refresh_from_db();self.assertIsNotNone(pilot.reference_locked_at)
        self.assertEqual(self.client.post(endpoint,{**payload,'summary-revision':1}).status_code,409)
        item.result=result;item.status='done';item.save()
        self.assertContains(self.client.get(url),'Revisar ficha generada')
        invalid={'phase':'result','block':'summary','summary-revision':0,'summary-decision':'corrected','summary-text':'Cambio sin respaldo','summary-quote':'No existe','summary-evidence_unit':'document'}
        self.assertEqual(self.client.post(endpoint,invalid).status_code,400)
        self.assertEqual(self.client.post(endpoint,{**payload,'phase':'result'}).status_code,302)
        corrected={**invalid,'summary-revision':1,'summary-quote':'Declárase de utilidad pública.'}
        self.assertEqual(self.client.post(endpoint,corrected).status_code,302)
        self.assertEqual(item.evaluations.filter(phase='result',block='summary').count(),2)
        self.assertContains(self.client.get(url),'Cambio sin respaldo')
        source=self.client.get(url+'fuente/')
        self.assertIn('sandbox',source['Content-Security-Policy'])
        self.assertEqual(source['X-Frame-Options'],'SAMEORIGIN')
        self.assertContains(self.client.get(response.url),'Informe del piloto')
        self.assertEqual(self.client.get(f'/gestion/pilotos/{pilot.pk}/exportar/').status_code,200)
        protected=Client(enforce_csrf_checks=True);protected.force_login(admin)
        self.assertEqual(protected.post(endpoint,payload).status_code,403)
        self.client.force_login(validator)
        for route in (url,url+'fuente/',response.url,f'/gestion/pilotos/{pilot.pk}/exportar/'):
            self.assertEqual(self.client.get(route).status_code,403)

    def test_direct_analysis_and_resume_old_reference_without_losing_decisions(self):
        from .pilot_contract import BLOCKS
        admin = User.objects.create_user('pilot-direct',is_staff=True)
        self.client.force_login(admin)
        now = timezone.now()
        norm = Norm.objects.create(infoleg_id=1,norm_type='Ley',number='23600',canonical_title='EXPROPIACIONES',
            general_status='desconocida',index_status='solo_texto_original',article_count=1)
        run = CrawlRun.objects.create(requested_ids=[1],started_at=now,finished_at=now,status='ok',processed_count=1)
        submission = uuid.uuid4()
        response = self.client.post('/gestion/pilotos/',{'run':run.pk,'model':DEFAULT_MODEL,'submission_id':submission,**LIMITS})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post('/gestion/pilotos/',{'run':run.pk,'model':DEFAULT_MODEL,'submission_id':submission,**LIMITS}).url,response.url)
        pilot = AnalysisPilot.objects.get()
        self.assertFalse(pilot.config['reference_required'])
        pilot.config.pop('reference_required')  # Un piloto creado con la versión anterior.
        pilot.status='reference';pilot.save()
        pilot.job.status='done';pilot.job.save()
        snapshot={'identity':{'type':'Ley','number':'23600','infoleg_id':1,'title':'EXPROPIACIONES'},
            'source':{'html':'<p>Declárase de utilidad pública.</p>','version':'original','retrieved_at':now.isoformat()},
            'integrity':{'status':'coincide'},'units':[{'id':'document','kind':'document','label':'Texto completo','text':'Declárase de utilidad pública.'}],
            'resources_not_checked':[{'kind':'img','url':'https://servicios.infoleg.gob.ar/infolegInternet/imagenes/left.png'}]}
        result=empty_result();result['summary']={'text':'Declara utilidad pública.','evidence':[{'unit_id':'document','quote':'Declárase de utilidad pública.'}]}
        result['articles']=[{'unit_id':'document','function':'Declara utilidad pública.'}]
        item=AnalysisPilotItem.objects.create(pilot=pilot,norm=norm,snapshot=snapshot,reference=result,split='development',fingerprint='direct-fixture')
        url=f'/gestion/pilotos/{pilot.pk}/normas/{item.pk}/'
        payload={'phase':'reference','block':'summary','summary-revision':0,'summary-decision':'confirmed'}
        self.assertEqual(self.client.post(url+'evaluar/',payload).status_code,302)
        self.assertContains(self.client.get(response.url),'Analizar ahora')
        self.assertNotContains(self.client.get(url),'Revisar ficha generada')
        self.assertEqual(self.client.post(response.url,{'action':'analyze_now'}).status_code,302)
        pilot.refresh_from_db();item.refresh_from_db();pilot.job.refresh_from_db()
        self.assertEqual(pilot.phase,'analyze');self.assertEqual(pilot.job.status,'queued')
        self.assertFalse(pilot.config['reference_required']);self.assertIsNone(pilot.reference_locked_at)
        self.assertEqual(item.reference,result);self.assertEqual(item.evaluations.count(),1)
        self.assertEqual(item.metadata.get('reference_decisions',{}),{})
        self.assertEqual(self.client.post(url+'evaluar/',{**payload,'summary-revision':1}).status_code,409)
        self.assertEqual(self.client.post(response.url,{'action':'analyze_now'}).status_code,400)
        item.result=result;item.status='done';item.save()
        page=self.client.get(url)
        self.assertContains(page,'Confirmar ficha y continuar')
        self.assertContains(page,'Ver función de los artículos · opcional')
        self.assertNotContains(page,'left.png')
        self.assertNotContains(page,'Hay anexos o recursos')
        self.assertNotContains(page,'Comparar con la referencia congelada')
        for block in BLOCKS:
            if block=='articles':continue  # Revisar la ficha no obliga a evaluar artículos.
            data={'phase':'result','block':block,f'{block}-revision':0,f'{block}-decision':'confirmed'}
            if block=='relations':data['relations-relation_count']=1
            self.assertEqual(self.client.post(url+'evaluar/',data).status_code,302)
        self.assertFalse(item.evaluations.filter(phase='result',block='articles').exists())
        report=self.client.get(response.url)
        self.assertContains(report,'1 revisadas')
        self.assertNotContains(report,'Comparación con la referencia revisada')
        self.assertContains(report,'no calcula exactitud')
        item.refresh_from_db();self.assertEqual(item.snapshot,snapshot)

    def test_model_selection_and_comparison_preserve_sources_results_and_votes(self):
        admin=User.objects.create_user('model-admin',is_staff=True)
        validator=User.objects.create_user('model-validator')
        self.client.force_login(admin)
        now=timezone.now()
        norm=Norm.objects.create(infoleg_id=1,norm_type='Ley',number='23600',canonical_title='EXPROPIACIONES',
            general_status='desconocida',index_status='solo_texto_original',article_count=1)
        run=CrawlRun.objects.create(requested_ids=[1],started_at=now,finished_at=now,status='ok',processed_count=1)
        data={'run':run.pk,'model':'test/alternate:free','submission_id':uuid.uuid4(),'max_tokens':49152,'reasoning_tokens':2048,'stages':['laya','llm']}
        for invalid in ('paid/model','unknown/model:free'):
            self.assertEqual(self.client.post('/gestion/pilotos/',{**data,'model':invalid}).status_code,400)
        self.assertEqual(AnalysisPilot.objects.count(),0)
        response=self.client.post('/gestion/pilotos/',data)
        self.assertEqual(response.status_code,302)
        original=AnalysisPilot.objects.get()
        self.assertEqual(original.model,'test/alternate:free')
        self.assertEqual(original.config['request_settings'],request_budget(49152,2048))
        self.assertEqual(self.client.post('/gestion/pilotos/',data).url,response.url)
        self.assertEqual(self.client.post('/gestion/pilotos/',{**data,'model':DEFAULT_MODEL}).status_code,400)
        self.assertEqual(self.client.post('/gestion/pilotos/',{**data,'max_tokens':65536}).status_code,400)
        self.assertEqual(AnalysisPilot.objects.count(),1)
        job=original.job;job.status='done';job.save()
        original.status='waiting';original.phase='analyze';original.reference_locked_at=now
        original.config.update(cards_enabled=True,endpoint={'context_length':999},contract=VERSION,prompt=PROMPT_VERSION)
        original.coverage={'selected':1,'eligible':1};original.save()
        result=empty_result();result['summary']={'text':'Propuesta anterior.','evidence':[]}
        snapshot={'identity':{'type':'Ley','number':'23600'},'source':{'hash':'frozen-source'},'units':[]}
        item=AnalysisPilotItem.objects.create(pilot=original,norm=norm,status='error',result=result,reference=result,
            snapshot=snapshot,split='holdout',fingerprint='same-input',laya={'revision':'cached-model'},
            metadata={'attempts':[{'finish_reason':'length'}],'reference_authors':{'summary':'model-admin'},
                'reference_decisions':{'summary':'confirmed'},'search_queries':['expropiación']},error='Corte anterior.')
        AnalysisPilotEvaluation.objects.create(item=item,phase='result',block='summary',user_id=admin.pk,
            username=admin.username,decision='confirmed',value=result['summary'],revision=1,created_at=now)
        card=ReviewCard.objects.create(item=item,kind='topic',fingerprint='old-card',snapshot=snapshot,proposal={},created_at=now)
        CardResponse.objects.create(card=card,user_id=validator.pk,username=validator.username,action='answer',decision='skip',
            request_id=uuid.uuid4(),created_at=now)
        before_item=list(AnalysisPilotItem.objects.values());before_pilot=list(AnalysisPilot.objects.values())
        compare={'action':'compare_model','model':original.model,'submission_id':uuid.uuid4(),**LIMITS,'stages':['llm'],'items':[item.pk]}
        protected=Client(enforce_csrf_checks=True);protected.force_login(admin)
        self.assertEqual(protected.post(response.url,compare).status_code,403)
        self.client.force_login(validator);self.assertEqual(self.client.post(response.url,compare).status_code,403)
        self.client.force_login(admin)
        invalid=self.client.post(response.url,{**compare,'reasoning_tokens':32768})
        self.assertEqual(invalid.status_code,400);self.assertContains(invalid,'para la ficha JSON',status_code=400)
        initial=self.client.get(response.url).context['model_form']
        self.assertEqual(initial['max_tokens'].value(),49152);self.assertEqual(initial['reasoning_tokens'].value(),2048)
        job.status='running';job.save()
        self.assertEqual(self.client.post(response.url,compare).status_code,400)
        job.status='done';job.save()
        created=self.client.post(response.url,compare)
        self.assertEqual(created.status_code,302)
        copied=AnalysisPilot.objects.exclude(pk=original.pk).get()
        self.assertEqual(copied.model,original.model);self.assertEqual(copied.crawl_run_id,run.pk)
        self.assertEqual(copied.phase,'analyze');self.assertEqual(copied.job.status,'queued')
        self.assertEqual(copied.config,{'contract':VERSION,'prompt':PROMPT_VERSION,'reference_required':False,
            'source_pilot_id':original.pk,'source_item_ids':[item.pk],'stages':['llm'],'request_settings':request_budget(32768,4096)})
        self.assertEqual(copied.reference_locked_at,now);self.assertEqual(copied.coverage,{**original.coverage,'with_references':0,'updated':0})
        fresh=copied.items.get()
        self.assertEqual(fresh.snapshot,snapshot);self.assertEqual(fresh.reference,result)
        self.assertEqual(fresh.split,'holdout');self.assertEqual(fresh.laya,item.laya)
        self.assertEqual(fresh.fingerprint,item.fingerprint);self.assertEqual(fresh.status,'pending')
        self.assertFalse(fresh.result);self.assertFalse(fresh.error)
        self.assertEqual(fresh.metadata,{'reference_authors':{'summary':'model-admin'},'reference_decisions':{'summary':'confirmed'},'search_queries':['expropiación'],'laya_reused':True,'laya_source_item_id':item.pk})
        self.assertFalse(fresh.evaluations.exists());self.assertFalse(fresh.cards.exists())
        self.assertEqual(list(AnalysisPilotItem.objects.filter(pk=item.pk).values()),before_item)
        self.assertEqual(list(AnalysisPilot.objects.filter(pk=original.pk).values()),before_pilot)
        self.assertEqual(CardResponse.objects.count(),1);self.assertEqual(AnalysisPilotEvaluation.objects.count(),1)
        progress_url=f'/gestion/pilotos/{copied.pk}/avance/'
        copied.job.status='running';copied.job.progress={'pilot_current':{'item_id':fresh.pk,'stage':'llm'}}
        copied.job.save()
        live=self.client.get(progress_url)
        self.assertEqual(live.status_code,200);self.assertEqual(live['Cache-Control'],'no-store')
        self.assertTrue(live.json()['active'])
        self.assertIn('Procesando ahora',live.json()['sample_html']);self.assertIn('Reutilizado',live.json()['sample_html'])
        self.assertIn('LLM',live.json()['progress_html'])
        fresh.status='done';fresh.result=result;fresh.save()
        copied.job.status='done';copied.job.save()
        live=self.client.get(progress_url).json()
        self.assertFalse(live['active']);self.assertIn('Ficha lista',live['sample_html'])
        self.client.force_login(validator);self.assertEqual(self.client.get(progress_url).status_code,403)
        self.client.force_login(admin)
        self.assertEqual(self.client.post(response.url,compare).url,created.url)
        self.assertEqual(self.client.post(response.url,{**compare,'max_tokens':65536}).status_code,400)
        self.assertEqual(self.client.post(response.url,{**compare,'stages':['laya']}).status_code,400)
        self.assertEqual(AnalysisPilot.objects.count(),2);self.assertEqual(CrawlJob.objects.count(),2)

    def test_download_groups_are_stable_and_only_selected_norms_are_analyzed(self):
        admin=User.objects.create_user('group-admin',is_staff=True);self.client.force_login(admin)
        now=timezone.now()
        run=CrawlRun.objects.create(requested_ids=list(range(500,531)),started_at=now,finished_at=now,status='ok',processed_count=31)
        norms=[Norm.objects.create(infoleg_id=i,norm_type='Ley',number=str(i),canonical_title='Norma de prueba',
            general_status='desconocida',index_status='completa',article_count=1) for i in range(500,531)]
        CrawlItem.objects.bulk_create([CrawlItem(crawl_run=run,norm=n,infoleg_id=n.infoleg_id,status='completa') for n in norms])
        response=self.client.post('/gestion/pilotos/',{'action':'groups','run':run.pk})
        self.assertEqual(response.status_code,302)
        groups=list(AnalysisPilot.objects.order_by('id'))
        self.assertEqual([len(g.config['norm_ids']) for g in groups],[30,1])
        self.assertTrue(all(g.config['group'] and g.job.status=='queued' for g in groups))
        self.assertEqual(self.client.post('/gestion/pilotos/',{'action':'groups','run':run.pk}).url,response.url)
        self.assertEqual(AnalysisPilot.objects.count(),2)
        group=groups[0];group.status='selection';group.phase='select';group.save()
        group.job.status='done';group.job.save()
        snapshot={'identity':{'type':'Ley','number':'500','infoleg_id':500,'title':'Prueba'},'source':{'hash':'frozen'},'units':[],'candidates':[]}
        items=[AnalysisPilotItem.objects.create(pilot=group,norm=n,snapshot=snapshot,reference=empty_result(),split='development',fingerprint=str(n.pk)) for n in norms[:2]]
        page=self.client.get(response.url)
        self.assertEqual(page.context['selected_item_ids'],[])
        data={'action':'compare_model','model':DEFAULT_MODEL,'submission_id':uuid.uuid4(),**LIMITS,'stages':['llm']}
        self.assertEqual(self.client.post(response.url,data).status_code,400)
        self.assertEqual(self.client.post(response.url,{**data,'items':[999999]}).status_code,400)
        created=self.client.post(response.url,{**data,'items':[items[0].pk]})
        self.assertEqual(created.status_code,302)
        block=AnalysisPilot.objects.get(config__group_pilot_id=group.pk)
        self.assertEqual(list(block.items.values_list('norm_id',flat=True)),[norms[0].pk])
        self.assertEqual(block.coverage['selected'],1)
        self.assertEqual(self.client.post(response.url,{**data,'items':[items[1].pk]}).status_code,400)
        self.assertEqual(group.items.count(),2);self.assertEqual(AnalysisPilot.objects.count(),3)
        fresh=block.items.get();fresh.laya={'revision':'cached','relations':[]};fresh.status='done';fresh.result=empty_result();fresh.save()
        block.job.status='done';block.job.save()
        page=self.client.get(response.url)
        self.assertContains(page,'Ficha lista');self.assertContains(page,f'/normas/{fresh.pk}/')
        # LLM sola puede usar Laya ya terminado en un bloque del mismo grupo.
        repeated=self.client.post(response.url,{**data,'submission_id':uuid.uuid4(),'items':[items[0].pk]})
        self.assertEqual(repeated.status_code,302)
        latest=AnalysisPilot.objects.order_by('-id').first().items.get()
        self.assertEqual(latest.laya,fresh.laya);self.assertEqual(latest.metadata['laya_source_item_id'],fresh.pk)
