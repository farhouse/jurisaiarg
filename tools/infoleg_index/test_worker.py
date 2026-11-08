"""Integración con BD descartable; ejecutar según README, nunca sobre datos reales."""
import os
import unittest
import urllib.error
import uuid
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

import worker


@unittest.skipUnless(os.getenv('CRAWLER_TEST_DSN'), 'requiere BD test_ aislada')
class WorkerIntegrationTest(unittest.TestCase):
    def test_health_requires_this_worker_connection_and_lock(self):
        dsn = os.environ['CRAWLER_TEST_DSN']
        self.assertTrue(conninfo_to_dict(dsn).get('dbname', '').startswith('test_'))
        self.assertFalse(worker.check_health(dsn))
        # Otro trabajador no puede hacer pasar este contenedor por saludable.
        with psycopg.connect(dsn, application_name=worker.worker_name() + '-other', autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(72419, 1)')
            self.assertFalse(worker.check_health(dsn))
        with psycopg.connect(dsn, application_name=worker.worker_name(), autocommit=True) as own:
            self.assertFalse(worker.check_health(dsn))
            own.execute('SELECT pg_advisory_lock(72419, 1)')
            self.assertTrue(worker.check_health(dsn))
            own.execute('SELECT pg_advisory_unlock(72419, 1)')
            self.assertFalse(worker.check_health(dsn))
            own.execute('SELECT pg_advisory_lock(72419, 1)')
        self.assertFalse(worker.check_health(dsn))

    def test_pipeline_failures_restart_and_review_preservation(self):
        dsn = os.environ['CRAWLER_TEST_DSN']
        self.assertTrue(conninfo_to_dict(dsn).get('dbname', '').startswith('test_'))
        worker.initialize(dsn)
        worker.initialize(dsn)  # También sobre volúmenes existentes.
        with psycopg.connect(dsn) as con:
            def enqueue(start, end):
                return con.execute('''INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by)
                    VALUES (%s,%s,%s,'test') RETURNING id''', (uuid.uuid4(), start, end)).fetchone()[0]

            def fetch(url, timeout):
                if 'id=202' in url:
                    raise urllib.error.URLError('fallo simulado')
                html = '''<div id="Textos_Completos"><strong>Ley 900 HONORABLE CONGRESO</strong>
                  <span class="destacado">PRUEBA AISLADA</span>
                  <a href="anexos/201/texact.htm">Texto actualizado de la norma</a></div>'''
                if 'texact.htm' in url:
                    html = '<p>Artículo 1º — Texto de prueba sin cambios.</p>'
                return 200, html, html.encode()

            first = enqueue(201, 202)
            con.commit()
            # Solo se simula la red: crawler, parser y preparación usan PostgreSQL real.
            with patch('crawl_infoleg_pg.fetch', side_effect=fetch), patch('crawl_infoleg_pg.time.sleep'):
                self.assertTrue(worker.process_next(con, dsn))
            status, run_id = con.execute('SELECT status,crawl_run_id FROM crawl_jobs WHERE id=%s', (first,)).fetchone()
            self.assertEqual(status, 'partial')
            self.assertEqual(con.execute('SELECT processed_count FROM crawl_runs WHERE id=%s', (run_id,)).fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT count(*) FROM review_tasks WHERE crawl_run_id=%s', (run_id,)).fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT count(*) FROM article_versions').fetchone()[0], 1)
            self.assertTrue(con.execute("SELECT model_result ? 'documents' FROM review_tasks WHERE crawl_run_id=%s ORDER BY id LIMIT 1", (run_id,)).fetchone()[0])
            self.assertEqual(con.execute("SELECT column_default FROM information_schema.columns WHERE table_name='norm_reviews' AND column_name='checks'").fetchone()[0], "'{}'::jsonb")
            self.assertEqual(con.execute("SELECT column_default FROM information_schema.columns WHERE table_name='review_tasks' AND column_name='proposed_correction'").fetchone()[0], "'{}'::jsonb")
            task = con.execute('SELECT id FROM review_tasks WHERE crawl_run_id=%s ORDER BY id LIMIT 1', (run_id,)).fetchone()[0]
            con.execute("INSERT INTO norm_reviews(task_id,user_id,username,status,notes) VALUES (%s,1,'test','validada','evidencia')", (task,))
            con.execute("INSERT INTO review_drafts(task_id,user_id,data,revision) VALUES (%s,1,'{\"items\":{}}',3)", (task,))
            con.commit()
            worker.initialize(dsn)
            self.assertEqual(con.execute('SELECT revision FROM review_drafts WHERE task_id=%s', (task,)).fetchone()[0], 3)
            # Fallo en clasificación: retomar el mismo lote sin acceder a la red.
            process_id = con.execute("INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by,mode,crawl_run_id) VALUES (%s,201,202,'test','process',%s) RETURNING id", (uuid.uuid4(), run_id)).fetchone()[0]
            con.commit()
            with patch('worker.crawl_ids', side_effect=AssertionError('no descargar')), patch('worker.run_stage', side_effect=RuntimeError('modelo')):
                worker.process_next(con, dsn)
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s', (process_id,)).fetchone()[0], 'failed')
            con.execute("UPDATE crawl_jobs SET status='queued' WHERE id=%s", (process_id,))
            con.commit()
            with patch('worker.crawl_ids', side_effect=AssertionError('no descargar')), patch('worker.run_stage') as stage:
                worker.process_next(con, dsn)
                self.assertEqual([call.args[0] for call in stage.call_args_list], ['classify', 'embeddings'])
            self.assertEqual(con.execute('SELECT crawl_run_id,status FROM crawl_jobs WHERE id=%s', (process_id,)).fetchone(), (run_id, 'partial'))
            second = enqueue(201, 201)
            con.commit()
            with patch('crawl_infoleg_pg.fetch', side_effect=fetch), patch('crawl_infoleg_pg.time.sleep'):
                worker.process_next(con, dsn)
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s', (second,)).fetchone()[0], 'done')
            self.assertEqual(con.execute('SELECT status,notes FROM norm_reviews WHERE task_id=%s', (task,)).fetchone(), ('validada', 'evidencia'))
            failed = enqueue(203, 203)
            con.commit()
            with patch('worker.crawl_ids', side_effect=RuntimeError('simulado')):
                worker.process_next(con, dsn)
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s', (failed,)).fetchone()[0], 'failed')
            con.execute("UPDATE crawl_jobs SET status='running' WHERE id=%s", (failed,))
            queued = enqueue(204, 204)
            con.commit()
            worker.recover_interrupted(con)
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s', (failed,)).fetchone()[0], 'failed')
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s', (queued,)).fetchone()[0], 'queued')


if __name__ == '__main__':
    unittest.main()
