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
