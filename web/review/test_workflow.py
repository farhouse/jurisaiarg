from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .models import Article, ArticleVersion, CrawlItem, CrawlJob, CrawlRun, LegalChange, Norm, NormReview, NormSource, NormVersion, ReviewEvent, ReviewTask


class WorkflowTest(TransactionTestCase):
    tables = [Norm, CrawlRun, NormSource, NormVersion, Article, ArticleVersion, LegalChange, CrawlItem, ReviewTask, ReviewEvent, NormReview, CrawlJob]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Tablas jurídicas unmanaged: crear solo en la BD de pruebas aislada.
        with connection.schema_editor() as schema:
            for model in cls.tables:
                schema.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            for model in reversed(cls.tables):
                schema.delete_model(model)
        super().tearDownClass()

    def test_independent_reviews_counts_permissions_and_history(self):
        staff = User.objects.create_user("admin-test", is_staff=True)
        validator = User.objects.create_user("validator-test")
        second = User.objects.create_user("second-test")
        group = Group.objects.create(name="Validadores")
        validator.groups.add(group)
        second.groups.add(group)
        stranger = User.objects.create_user("reader-test")
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[151], started_at=now, finished_at=now, status="ok", processed_count=1, review_prepared_at=now)
        norm = Norm.objects.create(infoleg_id=151, norm_type="Ley", number="1", general_status="desconocida", index_status="solo_ficha", article_count=0)
        CrawlItem.objects.create(crawl_run=run, infoleg_id=151, norm=norm, status="solo_ficha")
        task = ReviewTask.objects.create(crawl_run=run, norm=norm, kind="indice", source_text="Ficha de prueba", decision="revision", reasons=["control_del_indice"], fingerprint="test-index", created_at=now)
        CrawlRun.objects.create(requested_ids=[201, 202], started_at=now, status="running", processed_count=0)
        url = f"/validar/{task.pk}/"
        detail = f"/gestion/leyes/{task.pk}/"
        self.assertContains(self.client.get("/"), "Las leyes son de todos.")
        self.assertContains(self.client.get("/validar/guia/"), "Guía del validador")
        for route in ("/trabajo/", "/gestion/", "/validar/", detail):
            self.assertEqual(self.client.get(route).status_code, 302)
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(url, {"status": "validada", "revision": 0}).status_code, 403)
        self.client.force_login(validator)
        for route in ("/gestion/", detail):
            self.assertEqual(self.client.get(route).status_code, 403)
        self.assertContains(self.client.get(url), "Ficha de prueba")
        self.assertEqual(self.client.post(url, {"status": "rechazada", "revision": 0, "notes": ""}).status_code, 400)
        self.assertEqual(NormReview.objects.count(), 0)
        self.assertEqual(self.client.post(url, {"status": "validada", "revision": 0, "notes": "Fuente comprobada"}).status_code, 302)
        vote = NormReview.objects.get(task=task, user_id=validator.pk)
        self.assertEqual((vote.status, vote.revision), ("validada", 1))
        self.assertEqual(self.client.post(url, {"status": "rechazada", "revision": 0, "notes": "Formulario viejo"}).status_code, 409)
        self.assertEqual(ReviewEvent.objects.count(), 1)
        self.assertEqual(self.client.get("/validar/?scope=queue").context["page"].paginator.count, 0)
        self.assertEqual(self.client.get("/validar/?scope=mine").context["page"].paginator.count, 1)
        # Otro usuario conserva su formulario inicial y puede discrepar.
        self.client.force_login(second)
        self.assertEqual(self.client.get("/validar/?scope=queue").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get(url).context["form"].initial["revision"], 0)
        self.assertEqual(self.client.post(url, {"status": "rechazada", "revision": 0, "notes": "Falta texto <script>alert(1)</script>"}).status_code, 302)
        self.assertEqual(NormReview.objects.count(), 2)
        self.client.force_login(staff)
        response = self.client.get("/gestion/?status=desacuerdo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(response.context["vote_count"], 2)
        self.assertEqual(response.context["disagreements"], 1)
        law = self.client.get(detail).context["task"]
        self.assertEqual((law.approved, law.rejected, law.pending_votes, law.reviewers_count), (1, 1, 0, 2))
        response = self.client.get(detail + "?status=rechazada")
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertContains(response, "&lt;script&gt;")
        self.assertEqual(self.client.get("/gestion/?status=sin_revision").context["page"].paginator.count, 0)
        self.assertEqual(self.client.get("/gestion/?q=999").context["page"].paginator.count, 0)
        self.assertEqual(self.client.get("/gestion/?q=151").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get(detail + "?status=invalid").status_code, 404)
        # Reabrir solo cambia el voto propio; no borra el de otra persona.
        self.client.force_login(validator)
        self.assertEqual(self.client.post(url, {"status": "pendiente", "revision": 1, "notes": "Volver a comprobar"}).status_code, 302)
        self.assertEqual(NormReview.objects.get(user_id=second.pk).status, "rechazada")
        self.assertEqual(self.client.get("/validar/?scope=queue").context["pending"], 1)
        # Renombrar la cuenta no produce otra identidad para los contadores.
        validator.username = "renamed-test"
        validator.save()
        self.client.force_login(validator)
        self.assertEqual(self.client.post(url, {"status": "validada", "revision": 2}).status_code, 302)
        self.assertEqual(NormReview.objects.count(), 2)
        self.assertEqual(NormReview.objects.get(user_id=validator.pk).revision, 3)
        self.assertEqual(ReviewEvent.objects.count(), 4)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(validator)
        self.assertEqual(protected.post(url, {"status": "validada", "revision": 3}).status_code, 403)
        # Pasajes y otros lotes no inflan los votos de esta norma en este lote.
        passage = ReviewTask.objects.create(crawl_run=run, norm=norm, kind="pasaje", source_text="Pasaje", decision="revision", fingerprint="passage", created_at=now)
        self.assertEqual(self.client.post(f"/validar/{passage.pk}/", {"status": "validada", "revision": 0}).status_code, 302)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(f"/gestion/leyes/{passage.pk}/").status_code, 404)
        other = CrawlRun.objects.create(requested_ids=[151], started_at=now, status="ok", processed_count=1)
        other_task = ReviewTask.objects.create(crawl_run=other, norm=norm, kind="indice", source_text="Otro lote", decision="revision", fingerprint="other", created_at=now)
        NormReview.objects.create(task=other_task, user_id=validator.pk, username=validator.username, status="pendiente", updated_at=now)
        response = self.client.get(f"/gestion/?run={run.pk}")
        self.assertEqual(response.context["vote_count"], 2)
        self.assertEqual(self.client.get(detail).context["task"].pending_votes, 0)
        self.assertEqual(self.client.get(f"/gestion/leyes/{other_task.pk}/").context["task"].pending_votes, 1)

    def test_validator_management_permissions_accounts_and_activity(self):
        admin = User.objects.create_user("platform-admin", is_staff=True)
        reader = User.objects.create_user("platform-reader")
        group = Group.objects.create(name="Validadores")
        url = "/gestion/validadores/"
        create = url + "nuevo/"
        payload = {"username": "maria", "first_name": "María", "email": "maria@example.invalid", "password1": "River-papers-94!", "password2": "River-papers-94!"}
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(reader)
        for route in (url, create, url + f"{admin.pk}/"):
            self.assertEqual(self.client.get(route).status_code, 403)
            self.assertEqual(self.client.post(route, payload).status_code, 403)
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), "Todavía no hay validadores")
        self.assertContains(self.client.get(create), "Crear validador")
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(admin)
        self.assertEqual(protected.post(create, payload).status_code, 403)
        mismatch = self.client.post(create, {**payload, "password2": "different"})
        self.assertEqual(mismatch.status_code, 400)
        self.assertContains(mismatch, 'id="id_password2_error"', count=1, status_code=400)
        self.assertEqual(self.client.post(create, {**payload, "password1": "12345678", "password2": "12345678"}).status_code, 400)
        self.assertFalse(User.objects.filter(username="maria").exists())
        response = self.client.post(create, {**payload, "is_staff": True, "is_superuser": True, "user_permissions": "all"})
        self.assertEqual(response.status_code, 302)
        account = User.objects.get(username="maria")
        detail = url + f"{account.pk}/"
        self.assertEqual(response.url, detail)
        self.assertTrue(account.check_password(payload["password1"]))
        self.assertFalse(account.is_staff or account.is_superuser or account.user_permissions.exists())
        self.assertTrue(account.groups.filter(pk=group.pk).exists())
        self.assertEqual(self.client.post(create, payload).status_code, 400)
        for target in (admin, reader):
            self.assertEqual(self.client.post(url + f"{target.pk}/", {"is_active": ""}).status_code, 404)
        admin.groups.add(group)
        self.assertEqual(self.client.get(url + f"{admin.pk}/").status_code, 404)
        self.assertEqual(protected.post(detail, {"is_active": ""}).status_code, 403)
        self.assertEqual(self.client.get(detail + "?is_active=false").status_code, 200)
        account.refresh_from_db()
        self.assertTrue(account.is_active)
        self.client.force_login(account)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(detail, {"is_active": "on"}).status_code, 403)
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[151], started_at=now, status="ok", processed_count=1)
        norm = Norm.objects.create(infoleg_id=151, norm_type="Ley", number="23750", general_status="desconocida", index_status="solo_ficha", article_count=0)
        task = ReviewTask.objects.create(crawl_run=run, norm=norm, kind="indice", source_text="Ficha", decision="revision", fingerprint="account-index", created_at=now)
        vote = NormReview.objects.create(task=task, user_id=account.pk, username=account.username, status="validada", notes="Fuente <script>insegura</script>", updated_at=now)
        passage = ReviewTask.objects.create(crawl_run=run, norm=norm, kind="pasaje", source_text="Pasaje", decision="revision", fingerprint="account-passage", created_at=now)
        ReviewEvent.objects.create(task=passage, username=account.username, previous_status="pendiente", status="rechazada", notes="Falta contexto", created_at=now)
        self.client.force_login(admin)
        listing = self.client.get(url)
        self.assertEqual(listing.context["page"][0].review_counts, {"user_id": account.pk, "approved": 1, "rejected": 0, "pending": 0})
        self.assertEqual(self.client.get(url + "?q=maria").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get(url + "?q=nadie").context["page"].paginator.count, 0)
        detail_response = self.client.get(detail)
        self.assertContains(detail_response, "&lt;script&gt;")
        self.assertContains(detail_response, "Falta contexto")
        self.assertContains(detail_response, f'/gestion/leyes/{task.pk}/')
        self.assertEqual(detail_response.context["page"].paginator.count, 1)
        self.assertNotContains(listing, "/admin/auth/user/")
        self.assertEqual(self.client.post(detail, {"email": "not-an-email"}).status_code, 400)
        session = Client()
        session.force_login(account)
        self.assertEqual(self.client.post(detail, {"first_name": "María actualizada", "email": account.email, "username": "changed", "is_staff": "on", "is_superuser": "on"}).status_code, 302)
        account.refresh_from_db()
        self.assertFalse(account.is_active or account.is_staff or account.is_superuser)
        self.assertEqual(account.username, "maria")
        self.assertEqual(account.first_name, "María actualizada")
        self.assertEqual(session.get("/validar/").status_code, 302)
        self.assertFalse(session.login(username="maria", password=payload["password1"]))
        self.assertEqual(NormReview.objects.get(pk=vote.pk).status, "validada")
        self.assertEqual(ReviewEvent.objects.filter(task=passage, username=account.username).count(), 1)
        self.assertEqual(self.client.get(url + "?state=inactive").context["page"].paginator.count, 1)
        self.assertEqual(self.client.post(detail, {"is_active": "on"}).status_code, 302)
        self.assertTrue(session.login(username="maria", password=payload["password1"]))

    def test_z_topics_and_snapshot_are_reviewable_and_escaped(self):
        user = User.objects.create_user('topic-reviewer', is_staff=True)
        self.client.force_login(user)
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[9876], started_at=now, status='ok', processed_count=1)
        norm = Norm.objects.create(infoleg_id=9876, norm_type='Ley', number='demo', general_status='desconocida', index_status='completa', article_count=1)
        task = ReviewTask.objects.create(crawl_run=run, norm=norm, kind='pasaje', source_text='<script>evidencia</script>', decision='revision', fingerprint='topic-review', created_at=now,
            model_result={'category':'temas','matters':[{'label':'Laboral','score':.9}], 'concepts':['Salario']})
        page = self.client.get(f'/validar/{task.pk}/')
        self.assertContains(page, 'Revisar materias y conceptos.')
        self.assertContains(page, 'Laboral')
        self.assertContains(page, '&lt;script&gt;')
        self.assertNotContains(page, '<script>evidencia')
        self.assertEqual(self.client.post(f'/validar/{task.pk}/', {'status':'rechazada','revision':0,'notes':'El tema central es tributario.'}).status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.review_status, 'rechazada')
        task.kind='indice'
        task.model_result={'articles':[{'label':'1','version':'original','text':'Texto guardado inmutable'}]}
        task.save()
        self.assertContains(self.client.get(f'/validar/{task.pk}/?step=articles'), 'Texto guardado inmutable')

    def test_zz_norm_tabs_scope_decisions_and_empty_states(self):
        import uuid
        from .workflow import review_url
        user = User.objects.create_user('tabs-reviewer', is_staff=True)
        self.client.force_login(user)
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[98001], started_at=now, status='ok', processed_count=1)
        norm = Norm.objects.create(infoleg_id=98001, norm_type='Ley', number='23001', general_status='desconocida', index_status='completa', article_count=1)
        def case(kind, fingerprint, **kwargs):
            return ReviewTask.objects.create(crawl_run=run, norm=norm, kind=kind, source_text=fingerprint,
                decision='revision', fingerprint=fingerprint, created_at=now, **kwargs)
        copy = case('indice', 'tabs-copy')
        topic = case('pasaje', 'tabs-topic', model_result={'category': 'temas', 'concepts': ['Salario']})
        topic2 = case('pasaje', 'tabs-topic2', model_result={'category': 'temas'})
        relation = case('pasaje', 'tabs-relation')
        base = f'/validar/{copy.pk}/'
        response = self.client.get(base)
        self.assertEqual([(t['key'], t['total']) for t in response.context['tabs']], [('copy', 1), ('topics', 2), ('relations', 1)])
        self.assertContains(response, 'Materias y tags')
        self.assertContains(response, 'Relaciones')
        # Old passage links still open that exact passage in its section.
        self.assertEqual(self.client.get(f'/validar/{topic2.pk}/').context['task'].pk, topic2.pk)
        topic_url = review_url(copy.pk, 'topics', topic.pk)
        response = self.client.get(topic_url)
        self.assertContains(response, 'Salario')
        self.assertEqual(response.context['next_case']['id'], topic2.pk)
        self.assertEqual(response.context['task'].pk, topic.pk)
        # A POST to the root URL updates only the selected case, with revision protection.
        response = self.client.post(topic_url, {'status': 'validada', 'revision': 0, 'continue': 1})
        self.assertEqual(response.url, review_url(copy.pk, 'topics', topic2.pk))
        topic.refresh_from_db()
        copy.refresh_from_db()
        self.assertEqual(topic.review_status, 'validada')
        self.assertEqual(copy.review_status, 'pendiente')
        self.assertFalse(NormReview.objects.filter(task=copy).exists())
        self.assertEqual(self.client.post(topic_url, {'status': 'validada', 'revision': 0}).status_code, 409)
        self.assertEqual(ReviewEvent.objects.filter(task=topic).count(), 1)
        copy_url = review_url(copy.pk, 'copy', copy.pk)
        self.assertEqual(self.client.post(copy_url, {'status': 'validada', 'revision': 0}).url, copy_url)
        response = self.client.post(review_url(copy.pk, 'topics', topic2.pk), {'status': 'rechazada', 'revision': 0, 'notes': 'Falta una materia', 'continue': 1})
        self.assertEqual(response.url, review_url(copy.pk, 'relations', relation.pk))
        response = self.client.get(base + '?tab=topics')
        self.assertEqual(response.context['active_tab']['reviewed'], 2)
        # Another norm/run/section cannot be read or changed through this workspace.
        other_norm = Norm.objects.create(infoleg_id=98002, norm_type='Ley', number='23002', general_status='desconocida', index_status='solo_ficha', article_count=0)
        foreign = case('pasaje', 'tabs-foreign', model_result={'category': 'temas'})
        foreign.norm = other_norm
        foreign.save()
        other_run = CrawlRun.objects.create(requested_ids=[98001], started_at=now, status='ok', processed_count=1)
        foreign_run = case('pasaje', 'tabs-foreign-run', model_result={'category': 'temas'})
        foreign_run.crawl_run = other_run
        foreign_run.save()
        for selected in (foreign.pk, foreign_run.pk, relation.pk, 'invalid'):
            url = base + f'?tab=topics&case={selected}'
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {'status': 'validada', 'revision': 0}).status_code, 404)
        self.assertEqual(self.client.get(base + '?tab=invalid').status_code, 404)
        self.assertEqual(self.client.get(base + '?tab=topics').context['active_tab']['total'], 2)
        # Empty sections remain visible and never imply approval.
        empty_url = f'/validar/{foreign.pk}/?tab=relations'
        self.assertContains(self.client.get(empty_url), 'Pendiente de procesamiento')
        self.assertEqual(self.client.post(empty_url, {'status': 'validada', 'revision': 0}).status_code, 404)
        job = CrawlJob.objects.create(submission_id=uuid.uuid4(), start_id=98001, end_id=98002,
            mode='process', crawl_run=run, status='running', requested_by=user.username, created_at=now)
        self.assertContains(self.client.get(empty_url), 'Esperando resultados del procesamiento')
        self.assertContains(self.client.get(base), 'contadores todavía son parciales')
        job.status = 'failed'
        job.save()
        self.assertContains(self.client.get(empty_url), 'Procesamiento interrumpido')
        job.status = 'done'
        job.save()
        self.assertContains(self.client.get(empty_url), 'Sin casos generados')

    def test_zzz_guided_copy_controls_source_isolation_and_resume(self):
        admin = User.objects.create_user('guided-admin', is_staff=True)
        other = User.objects.create_user('guided-other', is_staff=True)
        reader = User.objects.create_user('guided-reader')
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[99001], started_at=now, finished_at=now, status='ok', processed_count=1)
        norm = Norm.objects.create(infoleg_id=99001, norm_type='Ley', number='23600', general_status='desconocida', index_status='solo_texto_original', article_count=2)
        task = ReviewTask.objects.create(crawl_run=run, norm=norm, kind='indice', source_text='Ficha de ejemplo', decision='revision', fingerprint='guided-copy', created_at=now, model_result={
            'identity': {'type': 'Ley', 'number': '23600', 'issuer': 'Congreso'},
            'articles': [{'label': '1', 'version': 'original', 'text': 'Artículo 1. Texto extraído.'},
                         {'label': '2', 'version': 'original', 'text': 'Artículo 2. Comuníquese.'}],
            'documents': [{'version': 'original', 'text': 'Texto completo\nANEXO I\nContenido del anexo.'}]})
        card = NormSource.objects.create(norm=norm, crawl_run=run, url=norm.infoleg_url,
            document_kind='ficha', retrieved_at=now, http_status=200, raw_content='<h1>Ley 23600</h1>')
        source = NormSource.objects.create(norm=norm, crawl_run=run,
            url='https://servicios.infoleg.gob.ar/infolegInternet/anexos/0-4999/1/norma.htm',
            document_kind='texto_original', retrieved_at=now, http_status=200,
            raw_content='<!doctype html><html><head><style>body { color: black; }</style></head><body><h1>Ley 23600</h1><p>Artículo 1. Texto de la fuente.</p><p>Artículo 2. Comuníquese.</p><h2>ANEXO I</h2><script>parent.document.body.remove()</script></body></html>')
        source_url = f'/validar/{task.pk}/fuente/{source.pk}/'
        self.assertEqual(self.client.get(source_url).status_code, 302)
        self.client.force_login(reader)
        self.assertEqual(self.client.get(source_url).status_code, 403)
        self.client.force_login(admin)
        view = self.client.get(source_url + '?focus=2')
        self.assertContains(view, '<span id="jurisai-focus">Artículo 2</span>.')
        self.assertTrue(view.content.startswith(b'<!doctype html>'))
        self.assertContains(view, '<head><base href=')
        self.assertEqual(view['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn("default-src 'none'", view['Content-Security-Policy'])
        self.assertIn('; sandbox', view['Content-Security-Policy'])
        self.assertEqual(view['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.post(source_url).status_code, 405)
        other_run = CrawlRun.objects.create(requested_ids=[99001], started_at=now, status='ok', processed_count=1)
        foreign = NormSource.objects.create(norm=norm, crawl_run=other_run, url=norm.infoleg_url,
            document_kind='texto_original', retrieved_at=now, http_status=200, raw_content='<p>Fuente de otro lote</p>')
        self.assertEqual(self.client.get(f'/validar/{task.pk}/fuente/{foreign.pk}/').status_code, 404)
        url = f'/validar/{task.pk}/'
        page = self.client.get(url)
        self.assertContains(page, 'Comparar textos, deben ser iguales.')
        self.assertContains(page, 'sandbox=""')
        self.assertEqual(page.context['copy']['checked_count'], 0)
        checks = page.context['copy']['checks']
        self.assertEqual(len(checks), 4)
        revision = 0
        for index, check in enumerate(checks):
            response = self.client.post(check['url'], {'check': check['key'], 'status': 'validada', 'revision': revision, 'continue': 1})
            self.assertEqual(response.status_code, 302)
            revision += 1
            vote = NormReview.objects.get(task=task, user_id=admin.pk)
            self.assertEqual(vote.status, 'validada' if index == len(checks) - 1 else 'pendiente')
            self.assertEqual(vote.checks[check['key']]['source_id'], card.pk if index == 0 else source.pk)
        self.assertIn('step=summary', response.url)
        summary = self.client.get(response.url)
        self.assertEqual(summary.context['copy']['checked_count'], 4)
        annex = self.client.get(checks[-1]['url'])
        self.assertContains(annex, 'Contenido del anexo.')
        article = checks[1]
        # A confirmed difference remains visible after returning to another step.
        rejected = self.client.post(article['url'], {'check': article['key'], 'status': 'rechazada', 'revision': revision, 'issues': ['missing_text']})
        self.assertEqual(rejected.status_code, 302)
        vote.refresh_from_db()
        self.assertEqual(vote.status, 'rechazada')
        self.assertEqual(vote.checks[article['key']]['issues'], ['missing_text'])
        event = ReviewEvent.objects.filter(task=task).last()
        self.assertEqual(event.details['check'], article['key'])
        self.assertEqual(event.details['status'], 'rechazada')
        self.assertIn('Falta texto', event.notes)
        resumed = self.client.get(article['url'])
        self.assertEqual(resumed.context['form'].initial['status'], 'rechazada')
        self.assertEqual(self.client.post(article['url'], {'check': article['key'], 'status': 'validada', 'revision': revision}).status_code, 409)
        self.assertEqual(self.client.post(article['url'], {'check': 'invented', 'status': 'validada', 'revision': vote.revision}).status_code, 400)
        self.assertEqual(self.client.post(article['url'], {'check': article['key'], 'status': 'validada', 'revision': vote.revision, 'issues': ['missing_text']}).status_code, 400)
        # Independent identity and progress for the next reviewer.
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).context['copy']['checked_count'], 0)
        self.assertEqual(NormReview.objects.filter(task=task).count(), 1)
        # No stored source may be replaced with one from a different run.
        source.raw_content = ''
        source.save()
        missing = self.client.get(article['url'])
        self.assertContains(missing, 'No hay una fuente disponible para comparar')
        self.assertEqual(self.client.post(article['url'], {'check': article['key'], 'status': 'validada', 'revision': 0}).status_code, 400)
        self.assertFalse(NormReview.objects.filter(task=task, user_id=other.pk).exists())
        source.raw_content = '%PDF-1.7 archivo binario'
        source.save()
        self.assertIsNone(self.client.get(article['url']).context['copy']['source'])
        self.assertEqual(self.client.get(source_url).status_code, 404)
        for query in ('?step=unknown', '?step=articles&article=-1', '?step=articles&article=garbage', '?version=invalid'):
            self.assertEqual(self.client.get(url + query).status_code, 404)

    def test_zzzz_topics_proposals_are_separate_and_conflict_safe(self):
        first = User.objects.create_user('proposal-first', is_staff=True)
        second = User.objects.create_user('proposal-second', is_staff=True)
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[99101], started_at=now, status='ok', processed_count=1)
        norm = Norm.objects.create(infoleg_id=99101, norm_type='Ley', number='23600', general_status='desconocida', index_status='solo_ficha', article_count=0)
        original = {'category': 'temas', 'matters': [{'label': 'Administrativo', 'score': .9}, {'label': 'Laboral', 'score': .8}], 'concepts': ['Expropiación', 'Inmueble']}
        task = ReviewTask.objects.create(crawl_run=run, norm=norm, kind='pasaje', source_text='Expropiación de inmueble con indemnización.', model_result=original, decision='revision', fingerprint='proposal-topics', created_at=now)
        url = f'/validar/{task.pk}/'
        self.client.force_login(first)
        page = self.client.get(url)
        self.assertContains(page, 'Revisar materias y conceptos.')
        self.assertEqual(page.context['form'].initial['keep_matters'], ['Administrativo', 'Laboral'])
        payload = {'proposal_kind': 'topics', 'revision': 0, 'status': 'rechazada', 'keep_matters': ['Administrativo'], 'keep_concepts': ['Expropiación', 'Inmueble'], 'add_concepts': 'Indemnización, indemnización', 'add_matters': '', 'issues': ['irrelevant']}
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(first)
        self.assertEqual(protected.post(url, payload).status_code, 403)
        self.assertEqual(self.client.post(url, {**payload, 'status': 'validada'}).status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, 'proposal_kind': 'relations'}).status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, 'keep_matters': ['Inventada']}).status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, 'add_concepts': 'x' * 81}).status_code, 400)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.model_result, original)
        self.assertEqual(task.proposed_correction['matters'], ['Administrativo'])
        self.assertEqual(task.proposed_correction['concepts'], ['Expropiación', 'Inmueble', 'Indemnización'])
        self.assertEqual(task.review_status, 'rechazada')
        self.assertEqual(task.revision, 1)
        event = ReviewEvent.objects.get(task=task)
        self.assertEqual(event.details['original']['matters'], ['Administrativo', 'Laboral'])
        self.assertIn('Quitar materia: Laboral', event.details['changes'])
        reopened = self.client.get(url)
        self.assertEqual(reopened.context['form'].initial['add_concepts'], 'Indemnización')
        self.assertEqual(reopened.context['form'].initial['issues'], ['irrelevant'])
        self.client.force_login(second)
        self.assertEqual(self.client.post(url, payload).status_code, 409)
        self.assertEqual(ReviewEvent.objects.filter(task=task).count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.reviewed_by, first.username)
        # Any proposed text remains inert in HTML, including the original JSON block.
        malicious = '<img src=x onerror=alert(1)>'
        self.assertEqual(self.client.post(url, {**payload, 'revision': 1, 'add_concepts': malicious}).status_code, 302)
        escaped = self.client.get(url)
        self.assertContains(escaped, '&lt;img')
        self.assertNotContains(escaped, '<img src=x')
        self.assertEqual(NormReview.objects.filter(task=task).count(), 0)

    def test_zzzzz_relation_corrections_sources_and_no_change_cases(self):
        user = User.objects.create_user('relation-proposer', is_staff=True)
        self.client.force_login(user)
        now = timezone.now()
        run = CrawlRun.objects.create(requested_ids=[99201], started_at=now, status='ok', processed_count=1)
        def norm(infoleg_id, number):
            return Norm.objects.create(infoleg_id=infoleg_id, norm_type='Ley', number=number, general_status='desconocida', index_status='completa', article_count=1)
        affected, amending, unrelated = norm(99201, '10001'), norm(99202, '23600'), norm(99203, '99999')
        article = Article.objects.create(norm=affected, article_label='7', article_key='7', ordinal=1)
        version = NormVersion.objects.create(norm=affected, version_type='actualizada', is_current=True)
        av = ArticleVersion.objects.create(article=article, norm_version=version, text_original='Texto', text_normalized='Texto con nota de sustitución.', status='desconocido')
        change = LegalChange.objects.create(amending_norm=amending, affected_norm=affected, affected_article=article,
            source_article_version=av, amending_reference_raw='Ley 23600', amending_article_reference='2',
            change_type='sustituye', affected_scope='articulo', evidence_text='inciso b)', parser_version='test',
            confidence=.9, review_status='pendiente', fingerprint='relation-proposal-change', created_at=now, updated_at=now)
        original = {'prediction': 'sustituye'}
        task = ReviewTask.objects.create(crawl_run=run, norm=affected, article_version=av, legal_change=change, kind='pasaje',
            source_text='(Inciso b) sustituido por art. 2 de la Ley 23600)', decision='revision', model_result=original,
            prediction='sustituye', fingerprint='relation-proposal', created_at=now)
        source = NormSource.objects.create(norm=affected, crawl_run=run, url=affected.infoleg_url, document_kind='texto_actualizado', retrieved_at=now, http_status=200, raw_content='<p>Artículo 7. Texto con nota.</p>')
        other_run = CrawlRun.objects.create(requested_ids=[99202], started_at=now, status='ok', processed_count=1)
        counterpart = NormSource.objects.create(norm=amending, crawl_run=other_run, url=amending.infoleg_url, document_kind='texto_original', retrieved_at=now, http_status=200, raw_content='<p>Artículo 2. Sustitúyese el inciso b).</p>')
        stranger = NormSource.objects.create(norm=unrelated, crawl_run=run, url=unrelated.infoleg_url, document_kind='texto_original', retrieved_at=now, http_status=200, raw_content='<p>Otra norma.</p>')
        wrong_run = NormSource.objects.create(norm=affected, crawl_run=other_run, url=affected.infoleg_url, document_kind='texto_actualizado', retrieved_at=now, http_status=200, raw_content='<p>Otro lote.</p>')
        url = f'/validar/{task.pk}/'
        page = self.client.get(url)
        self.assertEqual(len(page.context['passage']['viewers']), 2)
        self.assertContains(page, 'Verificar la relación entre normas.')
        for src in (source, counterpart):
            self.assertEqual(self.client.get(f'/validar/{task.pk}/fuente/{src.pk}/').status_code, 200)
        for src in (stranger, wrong_run):
            self.assertEqual(self.client.get(f'/validar/{task.pk}/fuente/{src.pk}/').status_code, 404)
        initial = page.context['form'].initial
        payload = {key: value for key, value in initial.items() if value is not None}
        payload.update(status='rechazada', scope='inciso', scope_detail='b)', issues=['scope'])
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        task.refresh_from_db(); change.refresh_from_db()
        self.assertEqual(task.model_result, original)
        self.assertEqual(change.affected_scope, 'articulo')
        self.assertEqual(change.change_type, 'sustituye')
        self.assertEqual(task.proposed_correction['scope'], 'inciso')
        self.assertEqual(task.proposed_correction['scope_detail'], 'b)')
        saved_sources = ReviewEvent.objects.get(task=task).details['sources']
        self.assertEqual({row['id'] for row in saved_sources}, {source.pk, counterpart.pk})
        self.assertEqual(self.client.get(url).context['form'].initial['scope_detail'], 'b)')
        self.assertEqual(self.client.post(url, {**payload, 'revision': 1, 'status': 'validada'}).status_code, 400)
        # A no-change classification has no invented target or modifying norm.
        none = ReviewTask.objects.create(crawl_run=run, norm=affected, kind='pasaje', source_text='Cita informativa.', decision='sin_cambio_detectado', prediction='ninguno', fingerprint='relation-no-change', created_at=now)
        none_url = f'/validar/{none.pk}/'
        none_payload = dict(self.client.get(none_url).context['form'].initial)
        none_payload.update(status='validada', notes='Sin modificación expresa en el fragmento.')
        self.assertEqual(self.client.post(none_url, none_payload).status_code, 302)
        none.refresh_from_db()
        self.assertEqual(none.review_status, 'validada')
        self.assertEqual(none.proposed_correction['amending_reference'], '')
        self.assertEqual(self.client.get(url + '?section=invalid').status_code, 404)
