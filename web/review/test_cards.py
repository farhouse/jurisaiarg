from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import uuid

from django.contrib.auth.models import Group, User
from django.db import connection, close_old_connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .cards import active_answers, card_proposals, publish
from .models import (Norm, CrawlRun, CrawlJob, ReviewTask, NormReview, AnalysisPilot,
    AnalysisPilotItem, AnalysisPilotEvaluation, ReviewCard, CardResponse)
from .pilot_contract import DEFAULT_MODEL, empty_result, fingerprint
from . import test_workflow


class CardsTest(TransactionTestCase):
    tables=test_workflow.WorkflowTest.tables+[AnalysisPilot,AnalysisPilotItem,AnalysisPilotEvaluation,ReviewCard,CardResponse]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema:
            for model in cls.tables:schema.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            for model in reversed(cls.tables):schema.delete_model(model)
        super().tearDownClass()

    def tearDown(self):
        for model in reversed(self.tables):model.objects.all().delete()
        super().tearDown()

    def setUp(self):
        now=timezone.now()
        self.admin=User.objects.create_user('cards-admin',is_staff=True)
        self.user=User.objects.create_user('cards-validator')
        self.other=User.objects.create_user('cards-second')
        self.reader=User.objects.create_user('cards-reader')
        group=Group.objects.create(name='Validadores')
        self.user.groups.add(group);self.other.groups.add(group)
        self.client.force_login(self.user)
        self.norm=Norm.objects.create(infoleg_id=1,norm_type='Ley',number='23600',canonical_title='EXPROPIACIONES',general_status='desconocida',index_status='completa',article_count=4)
        self.run=CrawlRun.objects.create(requested_ids=[1],started_at=now,finished_at=now,status='ok',processed_count=1)
        self.job=CrawlJob.objects.create(submission_id=uuid.uuid4(),start_id=1,end_id=1,requested_by='admin',mode='pilot',crawl_run=self.run,created_at=now,status='done')
        self.pilot=AnalysisPilot.objects.create(job=self.job,crawl_run=self.run,model=DEFAULT_MODEL,status='complete',phase='analyze',created_at=now,config={'contract':'v1','prompt':'v1'})
        paragraphs=[
            'Artículo 1° — Declárase de utilidad pública y sujeto a expropiación con destino al Poder Judicial de la Nación, el inmueble ubicado en la calle Lavalle 1240/42/46/50 de esta Capital Federal.',
            'Art. 2° — Facúltase a la Corte Suprema de Justicia de la Nación para determinar las dependencias judiciales que se instalarán en dicho inmueble.',
            'Art. 3° — Los gastos necesarios para su adquisición se imputarán a la cuenta prevista en el artículo 19 de la Ley 21.859 de Tasas Judiciales.',
            'Art. 4° — Comuníquese al Poder Ejecutivo.']
        document='EXPROPIACIONES Ley 23600 '+' '.join(paragraphs)
        self.snapshot={'identity':{'type':'Ley','number':'23600','infoleg_id':1,'title':'EXPROPIACIONES'},
            'source':{'html':'<p>'+document+'</p><script>unsafe()</script>','hash':fingerprint(document),'version':'original','retrieved_at':now.isoformat()},
            'integrity':{'status':'coincide'},'resources_not_checked':[],
            'units':[{'id':'document','kind':'document','label':'Texto completo','text':document}]+
                [{'id':f'article:{i}','kind':'article','label':str(i+1),'text':text} for i,text in enumerate(paragraphs)]}
        evidence=[{'unit_id':'article:0','quote':paragraphs[0]}]
        self.result=empty_result()
        self.result['summary']={'text':'Declara la expropiación de un inmueble para el Poder Judicial, faculta a la Corte a determinar sus dependencias y establece su financiación.',
            'evidence':[{'unit_id':f'article:{i}','quote':text} for i,text in enumerate(paragraphs[:3])]}
        self.result['concepts']=[{'label':label,'role':'central','evidence':evidence} for label in ('Expropiación','Utilidad pública')]
        self.result['matters']=[{'label':'Administrativo','primary':True,'evidence':evidence}]
        self.result['articles']=[{'unit_id':f'article:{i}','function':function} for i,function in enumerate(('Expropiación.','Dependencias judiciales.','Financiación.','Comunicación formal.'))]
        self.result['relations']=[{'source_unit_id':'article:2','direction':'outgoing','target_type':'Ley','target_number':'21859','target_article':'19','type':'cita','scope':'articulo','detail':'',
            'evidence':[{'unit_id':'article:2','quote':paragraphs[2]}]}]
        self.item=AnalysisPilotItem.objects.create(pilot=self.pilot,norm=self.norm,status='done',snapshot=self.snapshot,result=self.result,reference=self.result,split='development',fingerprint='cards-example')

    def post(self,card,decision,client=None,nonce=None,ajax=False):
        return (client or self.client).post(f'/validar/tarjetas/{card.pk}/responder/',{'request_id':nonce or uuid.uuid4(),'decision':decision},
            **({'HTTP_X_REQUESTED_WITH':'XMLHttpRequest'} if ajax else {}))

    def test_publication_is_versioned_idempotent_and_preserves_original_reviews(self):
        task=ReviewTask.objects.create(norm=self.norm,crawl_run=self.run,kind='indice',source_text='Original',decision='revision',fingerprint='prior-review',created_at=timezone.now())
        NormReview.objects.create(task=task,user_id=self.user.pk,username=self.user.username,status='validada',checks={'copy':True},updated_at=timezone.now())
        AnalysisPilotEvaluation.objects.create(item=self.item,phase='result',block='summary',user_id=self.admin.pk,username='admin',decision='confirmed',value=self.result['summary'],revision=1,created_at=timezone.now())
        before=list(NormReview.objects.values());source=deepcopy(self.item.snapshot)
        publish(self.pilot.pk,True);publish(self.pilot.pk,True)
        self.assertEqual(ReviewCard.objects.count(),3)
        self.assertEqual(CardResponse.objects.count(),0)
        card=ReviewCard.objects.filter(kind='topic').first()
        self.assertEqual(self.post(card,'correct').status_code,302)
        original_ids=set(ReviewCard.objects.values_list('id',flat=True))
        self.item.result['summary']['text']='Dispone la expropiación de un inmueble para el Poder Judicial.'
        self.item.metadata={'request_id':'otra-respuesta-del-modelo'};self.item.save()
        publish(self.pilot.pk,True)
        self.assertEqual(ReviewCard.objects.filter(active=True).count(),3)
        self.assertEqual(ReviewCard.objects.count(),4)
        self.assertTrue(ReviewCard.objects.get(pk=card.pk).active)
        self.assertTrue(original_ids <= set(ReviewCard.objects.values_list('id',flat=True)))
        self.item.refresh_from_db();self.assertEqual(self.item.snapshot,source)
        self.assertEqual(list(NormReview.objects.values()),before)
        self.assertEqual(AnalysisPilotEvaluation.objects.count(),1)
        publish(self.pilot.pk,False)
        self.assertFalse(ReviewCard.objects.filter(active=True).exists())
        self.assertEqual(active_answers().count(),1)
        publish(self.pilot.pk,True)
        self.assertEqual(ReviewCard.objects.count(),4)

    def test_summary_limits_and_invalid_evidence_are_explicit(self):
        proposals,reasons=card_proposals(self.item)
        self.assertEqual([k for k,v in proposals].count('summary'),1);self.assertFalse(reasons)
        for cause in ('source','summary','integrity','resource','evidence'):
            item=deepcopy(self.item)
            if cause=='source':item.snapshot['units'][0]['text']+='x'*4001
            elif cause=='summary':item.result['summary']['text']='x'*601
            elif cause=='integrity':item.snapshot['integrity']['status']='diferencias'
            elif cause=='resource':item.snapshot['resources_not_checked']=[{'kind':'img','url':'https://example.invalid/anexo.png'}]
            else:item.result['concepts'][0]['evidence'][0]['quote']='No existe'
            with self.subTest(cause=cause):
                proposals,reasons=card_proposals(item)
                self.assertTrue(reasons);self.assertFalse(any(kind=='summary' for kind,_ in proposals))

    def test_permissions_sources_abstentions_undo_comments_and_nonce_retries(self):
        publish(self.pilot.pk,True)
        self.client.logout();self.assertEqual(self.client.get('/validar/').status_code,302)
        self.client.force_login(self.reader);self.assertEqual(self.client.get('/validar/').status_code,403)
        self.client.force_login(self.user)
        card=ReviewCard.objects.filter(kind='topic').first()
        page=self.client.get('/validar/')
        self.assertContains(page,'Revisar un tema');self.assertNotContains(page,'Tu decisión sobre materias')
        nonce=uuid.uuid4()
        self.assertEqual(self.post(card,'skip',nonce=nonce,ajax=True).status_code,200)
        self.assertEqual(self.post(card,'skip',nonce=nonce,ajax=True).status_code,200)
        self.assertEqual(CardResponse.objects.count(),1)
        self.assertEqual(self.post(card,'correct',nonce=nonce).status_code,409)
        self.assertNotEqual(self.client.get('/validar/').context['card'].pk,card.pk)
        vote=active_answers().get()
        note=f'/validar/tarjetas/{card.pk}/comentar/'
        self.assertEqual(self.client.post(note,{'request_id':uuid.uuid4(),'answer_id':vote.pk,'notes':'No tengo contexto suficiente.'},HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code,200)
        self.assertEqual(active_answers().count(),1)
        source=self.client.get(f'/validar/tarjetas/{card.pk}/fuente/')
        self.assertEqual(source.status_code,200);self.assertIn('sandbox',source['Content-Security-Policy']);self.assertEqual(source['X-Frame-Options'],'SAMEORIGIN')
        second=Client();second.force_login(self.other)
        self.assertEqual(second.post(note,{'request_id':uuid.uuid4(),'answer_id':vote.pk,'notes':'Ajeno'}).status_code,409)
        self.assertEqual(self.post(card,'correct',client=second).status_code,302)
        undo=f'/validar/tarjetas/{card.pk}/deshacer/'
        undo_data={'request_id':uuid.uuid4(),'answer_id':vote.pk}
        next_card=ReviewCard.objects.exclude(pk=card.pk).first()
        self.assertEqual(self.post(next_card,'correct').status_code,302)
        newest=active_answers().filter(user_id=self.user.pk).order_by('-id').first()
        self.assertEqual(self.client.post(undo,undo_data).status_code,409)
        self.assertEqual(self.client.post(f'/validar/tarjetas/{next_card.pk}/deshacer/',{'request_id':uuid.uuid4(),'answer_id':newest.pk}).status_code,302)
        self.assertEqual(self.client.post(undo,undo_data).status_code,302)
        self.assertEqual(self.client.post(undo,undo_data).status_code,302)
        self.assertEqual(active_answers().filter(user_id=self.user.pk).count(),0)
        self.assertEqual(active_answers().filter(user_id=self.other.pk).count(),1)
        protected=Client(enforce_csrf_checks=True);protected.force_login(self.user)
        self.assertEqual(self.post(card,'correct',client=protected).status_code,403)
        self.assertEqual(self.client.get('/gestion/tarjetas/').status_code,403)
        self.assertEqual(self.client.post(f'/gestion/pilotos/{self.pilot.pk}/tarjetas/',{'action':'withdraw'}).status_code,403)
        publish(self.pilot.pk,False)
        self.assertEqual(self.client.get(f'/validar/tarjetas/{card.pk}/fuente/').status_code,404)
        self.assertEqual(self.post(card,'correct').status_code,409)

    def test_independent_answers_queue_priority_and_admin_counts(self):
        publish(self.pilot.pk,True)
        card=ReviewCard.objects.order_by('id').first()
        self.assertEqual(self.post(card,'correct').status_code,302)
        second=Client();second.force_login(self.other)
        self.assertNotEqual(second.get('/validar/').context['card'].pk,card.pk)
        self.assertEqual(self.post(card,'incorrect',client=second).status_code,302)
        summary=ReviewCard.objects.get(kind='summary')
        self.assertEqual(self.post(summary,'skip').status_code,302)
        self.client.force_login(self.admin)
        report=self.client.get(f'/gestion/tarjetas/?norm={self.norm.pk}')
        law=list(report.context['laws'])[0]
        self.assertEqual(law.card_totals,{'active':3,'correct':1,'incorrect':1,'skip':1,'disagreements':1,'unanswered':2})
        self.assertEqual(law.people,2)
        self.assertContains(report,'No sé');self.assertContains(report,'Historial de aportes')

    def test_same_user_two_tabs_cannot_duplicate_an_answer(self):
        publish(self.pilot.pk,True)
        card=ReviewCard.objects.first()
        clients=[Client(),Client()]
        for client in clients:client.force_login(self.user)
        barrier=threading.Barrier(2)
        def submit(i):
            close_old_connections();barrier.wait()
            try:return self.post(card,('correct','incorrect')[i],client=clients[i]).status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(submit,range(2)))
        self.assertEqual(sorted(statuses),[302,409])
        self.assertEqual(active_answers().count(),1)

    def test_admin_can_publish_own_corrections_without_rewriting_model_output(self):
        corrected={**self.result['summary'],'text':'Dispone la expropiación de un inmueble destinado al Poder Judicial.'}
        AnalysisPilotEvaluation.objects.create(item=self.item,phase='result',block='summary',user_id=self.admin.pk,
            username=self.admin.username,decision='corrected',value=corrected,revision=1,created_at=timezone.now())
        publish(self.pilot.pk,True,self.admin.pk)
        card=ReviewCard.objects.get(kind='summary')
        self.assertEqual(card.proposal['text'],corrected['text'])
        self.assertEqual(card.proposal['review']['user_id'],self.admin.pk)
        self.item.refresh_from_db();self.assertEqual(self.item.result,self.result)
        publish(self.pilot.pk,True,self.admin.pk)
        self.assertEqual(ReviewCard.objects.count(),3)
        AnalysisPilotEvaluation.objects.create(item=self.item,phase='result',block='concepts',user_id=self.admin.pk,
            username=self.admin.username,decision='legal',value=self.result['concepts'],notes='Requiere criterio jurídico.',revision=1,created_at=timezone.now())
        publish(self.pilot.pk,True,self.admin.pk)
        self.assertEqual(ReviewCard.objects.filter(active=True).count(),1)
