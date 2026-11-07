from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .models import Article, ArticleVersion, CrawlItem, CrawlRun, LegalChange, Norm, NormReview, NormVersion, ReviewEvent, ReviewTask


class WorkflowTest(TransactionTestCase):
    tables = [Norm, NormVersion, Article, ArticleVersion, LegalChange, CrawlRun, CrawlItem, ReviewTask, ReviewEvent, NormReview]

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
