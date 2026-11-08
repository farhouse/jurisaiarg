import uuid

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TransactionTestCase

from .models import CrawlJob, CrawlRun, Norm


class CrawlerAdminTest(TransactionTestCase):
    tables = [Norm, CrawlRun, CrawlJob]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema:
            for model in cls.tables:
                schema.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            for model in reversed(cls.tables):
                schema.delete_model(model)
        super().tearDownClass()

    def test_permissions_ranges_idempotence_and_status(self):
        url = '/gestion/descargas/'
        self.assertEqual(self.client.get(url).status_code, 302)
        validator = User.objects.create_user('crawler-validator-test')
        self.client.force_login(validator)
        payload = {'mode': 'full', 'start_id': 201, 'end_id': 250, 'submission_id': uuid.uuid4()}
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        admin = User.objects.create_user('crawler-admin-test', is_staff=True)
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), 'Desde el índice')
        for start, end in [(0, 50), (5, 4), (1, 1001), (1, 2147483648), ('abc', 10)]:
            self.assertEqual(self.client.post(url, {**payload, 'start_id': start, 'end_id': end}).status_code, 400)
        self.assertEqual(CrawlJob.objects.count(), 0)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(admin)
        self.assertEqual(csrf_client.post(url, payload).status_code, 403)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post(url, payload).url, response.url)
        self.assertEqual(CrawlJob.objects.count(), 1)
        job = CrawlJob.objects.get()
        self.assertEqual((job.start_id, job.end_id, job.total, job.requested_by), (201, 250, 50, admin.username))
        self.assertContains(self.client.get(response.url), 'En cola')
        self.assertContains(self.client.get(response.url), 'http-equiv="refresh"')
        from django.utils import timezone
        run = CrawlRun.objects.create(requested_ids=[201,202], started_at=timezone.now(), finished_at=timezone.now(), status='ok', processed_count=2)
        process = {'mode': 'process', 'run': run.pk, 'submission_id': uuid.uuid4()}
        response_process = self.client.post(url, process)
        self.assertEqual(response_process.status_code, 302)
        self.assertEqual(CrawlJob.objects.get(mode='process').crawl_run_id, run.pk)
        from .crawler import CrawlForm
        process_form = CrawlForm({**process, 'start_id': 'ignorar', 'end_id': -1})
        self.assertTrue(process_form.is_valid(), process_form.errors)
        self.assertEqual((process_form.cleaned_data['start_id'], process_form.cleaned_data['end_id']), (201, 202))
        for mode in ('full', 'download'):
            download_form = CrawlForm({**payload, 'mode': mode, 'run': 'lote-inexistente'})
            self.assertTrue(download_form.is_valid(), download_form.errors)
            self.assertIsNone(download_form.cleaned_data['run'])
        self.assertEqual(self.client.post(url, {**process, 'run': 999999, 'submission_id': uuid.uuid4()}).status_code, 400)
        job.status = 'failed'
        job.error = '<script>unsafe</script>'
        job.save()
        detail = self.client.get(response.url)
        self.assertContains(detail, '&lt;script&gt;')
        self.assertNotContains(detail, 'http-equiv="refresh"')
        self.client.force_login(validator)
        self.assertEqual(self.client.get(response.url).status_code, 403)
        self.assertEqual(self.client.post(response.url).status_code, 403)

    def test_status_distinguishes_progress_signal_and_terminal_states(self):
        from datetime import timedelta
        from django.utils import timezone
        admin = User.objects.create_user('activity-admin', is_staff=True)
        self.client.force_login(admin)
        now = timezone.now()
        job = CrawlJob.objects.create(submission_id=uuid.uuid4(), start_id=301, end_id=350,
            requested_by=admin.username, created_at=now, started_at=now-timedelta(minutes=12),
            status='running', stage='classify', heartbeat_at=now,
            last_activity_at=now-timedelta(minutes=6),
            progress={'activity': {'message': 'Descargando Laya', 'file': '<script>model</script>',
                                   'files_done': 2, 'files_total': 5}})
        url = f'/gestion/descargas/{job.pk}/'
        try:
            response = self.client.get(url)
            self.assertContains(response, 'Descargando Laya')
            self.assertContains(response, 'Sin avances registrados')
            self.assertContains(response, '&lt;script&gt;model&lt;/script&gt;')
            self.assertContains(response, '2 de 5')
            self.assertNotContains(response, 'No recibimos señal')
            job.heartbeat_at = now-timedelta(minutes=2)
            job.save()
            self.assertContains(self.client.get(url), 'No recibimos señal')
            for state in ('done', 'failed', 'queued'):
                job.status = state
                job.save()
                response = self.client.get(url)
                self.assertNotContains(response, 'Sin avances registrados')
                self.assertNotContains(response, 'No recibimos señal')
            job.status = 'running'
            job.heartbeat_at = job.last_activity_at = None
            job.save()
            self.assertContains(self.client.get(url), 'todavía no tiene señales')
        finally:
            job.delete()
