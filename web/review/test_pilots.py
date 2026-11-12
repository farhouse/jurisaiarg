import uuid
from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase,Client
from django.utils import timezone
from .models import Norm,CrawlRun,CrawlJob,AnalysisPilot,AnalysisPilotItem,AnalysisPilotEvaluation,ReviewCard,CardResponse
from .pilot_contract import empty_result,DEFAULT_MODEL


class PilotsTest(TransactionTestCase):
    tables = [Norm,CrawlRun,CrawlJob,AnalysisPilot,AnalysisPilotItem,AnalysisPilotEvaluation,ReviewCard,CardResponse]

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
        response = self.client.post('/gestion/pilotos/',{'run':run.pk,'submission_id':uuid.uuid4(),'reference_required':'on'})
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
        response = self.client.post('/gestion/pilotos/',{'run':run.pk,'submission_id':submission})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post('/gestion/pilotos/',{'run':run.pk,'submission_id':submission}).url,response.url)
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
