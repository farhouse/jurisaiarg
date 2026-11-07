import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

from job_activity import JobActivity, prepare_model


class ModelPreparationTest(unittest.TestCase):
    def test_cached_model_does_not_download(self):
        events = []
        with patch('huggingface_hub.hf_hub_download') as download:
            prepare_model('repo', 'revision', ['config.json', 'model.safetensors'], 'Laya',
                          lambda message, **details: events.append((message, details)))
        self.assertTrue(all(call.kwargs['local_files_only'] for call in download.call_args_list))
        self.assertEqual(events[-1], ('Cargando Laya en memoria', {'model': 'Laya', 'cached': True}))
        self.assertFalse(any(message == 'Descargando Laya' for message, _ in events))

    def test_partial_cache_and_download_failure_do_not_claim_loaded(self):
        from huggingface_hub.errors import LocalEntryNotFoundError
        for fail in (False, True):
            events = []
            def download(repo, filename, **kwargs):
                if filename == 'model.safetensors' and kwargs.get('local_files_only'):
                    raise LocalEntryNotFoundError('missing')
                if fail and not kwargs.get('local_files_only'):
                    raise OSError('network')
            with patch('huggingface_hub.hf_hub_download', side_effect=download) as fetch:
                if fail:
                    with self.assertRaises(OSError):
                        prepare_model('repo', 'revision', ['config.json', 'model.safetensors'], 'E5',
                                      lambda message, **details: events.append((message, details)))
                    self.assertEqual(events[-1][0], 'Descargando E5')
                else:
                    prepare_model('repo', 'revision', ['config.json', 'model.safetensors'], 'E5',
                                  lambda message, **details: events.append((message, details)))
                    self.assertEqual(events[-1][0], 'Cargando E5 en memoria')
                    self.assertFalse(events[-1][1]['cached'])
                self.assertEqual(fetch.call_count, 3)
                self.assertEqual(events[1][1]['files_total'], 1)


@unittest.skipUnless(os.getenv('CRAWLER_TEST_DSN'), 'requiere BD test_ aislada')
class ActivityIntegrationTest(unittest.TestCase):
    def test_heartbeat_is_not_progress_and_stops_for_finished_job(self):
        import worker
        dsn = os.environ['CRAWLER_TEST_DSN']
        self.assertTrue(conninfo_to_dict(dsn)['dbname'].startswith('test_'))
        worker.initialize(dsn)
        with psycopg.connect(dsn, autocommit=True) as con:
            job = con.execute("INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by,status) VALUES (%s,1,1,'test','running') RETURNING id", (uuid.uuid4(),)).fetchone()[0]
            try:
                with JobActivity(dsn, job) as report:
                    report('Procesando', document='InfoLEG 1')
                    before = con.execute('SELECT last_activity_at FROM crawl_jobs WHERE id=%s', (job,)).fetchone()[0]
                    # Una iteración real de señal, sin sleeps ni hilo en la prueba.
                    with patch.object(report.stop, 'wait', side_effect=lambda *_: report.stop.set()):
                        report._heartbeat()
                    after, heartbeat = con.execute('SELECT last_activity_at,heartbeat_at FROM crawl_jobs WHERE id=%s', (job,)).fetchone()
                    self.assertEqual(before, after)
                    self.assertIsNotNone(heartbeat)
                    con.execute("UPDATE crawl_jobs SET status='done' WHERE id=%s", (job,))
                    report('No debe sobrescribir el final')
                    self.assertEqual(con.execute("SELECT progress->'activity'->>'message' FROM crawl_jobs WHERE id=%s", (job,)).fetchone()[0], 'Procesando')
                    report.stop.clear()
                    with patch.object(report.stop, 'wait', side_effect=lambda *_: report.stop.set()):
                        report._heartbeat()
                    self.assertEqual(con.execute('SELECT heartbeat_at FROM crawl_jobs WHERE id=%s', (job,)).fetchone()[0], heartbeat)
            finally:
                con.execute('DELETE FROM crawl_jobs WHERE id=%s', (job,))
