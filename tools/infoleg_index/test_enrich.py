import os
import unittest
from unittest.mock import patch
import psycopg
from psycopg.conninfo import conninfo_to_dict
import worker
from enrich import embed_batch


@unittest.skipUnless(os.getenv('CRAWLER_TEST_DSN'), 'requiere BD test_ aislada')
class EnrichmentTest(unittest.TestCase):
    def test_resume_content_change_and_atomic_document(self):
        dsn = os.environ['CRAWLER_TEST_DSN']
        self.assertTrue(conninfo_to_dict(dsn)['dbname'].startswith('test_'))
        worker.initialize(dsn)
        with psycopg.connect(dsn) as con:
            norm = con.execute("INSERT INTO norms(infoleg_id,norm_type,number) VALUES (987654,'Ley','demo') RETURNING id").fetchone()[0]
            run = con.execute("INSERT INTO crawl_runs(source_id,requested_ids,status,finished_at) SELECT id,ARRAY[987654],'ok',now() FROM sources WHERE code='infoleg' RETURNING id").fetchone()[0]
            con.execute("INSERT INTO crawl_items(crawl_run_id,infoleg_id,norm_id,status) VALUES (%s,987654,%s,'solo_ficha')", (run,norm))
            chunk = con.execute("INSERT INTO search_chunks(norm_id,chunk_type,content) VALUES (%s,'titulo','Texto uno') RETURNING id", (norm,)).fetchone()[0]
            con.commit()
            vector = [1.] + [0.] * 383
            with patch('enrich.fragments', return_value=['Texto uno']), patch('enrich.encode', return_value=[vector]) as encode:
                embed_batch(con, run, -1)
                embed_batch(con, run, -1)
                self.assertEqual(encode.call_count, 1)
            con.execute("UPDATE search_chunks SET content='Texto dos' WHERE id=%s", (chunk,))
            con.commit()
            self.assertEqual(con.execute('SELECT count(*) FROM semantic_passages p JOIN search_chunks c ON c.id=p.chunk_id WHERE p.source_hash=md5(c.content) AND c.id=%s', (chunk,)).fetchone()[0], 0)
            con.commit()
            with patch('enrich.fragments', return_value=['Texto', 'dos']), patch('enrich.encode', side_effect=[[vector], RuntimeError('fallo')]):
                with self.assertRaises(RuntimeError):
                    embed_batch(con, run, -1)
            self.assertEqual(con.execute('SELECT count(*) FROM semantic_passages WHERE chunk_id=%s', (chunk,)).fetchone()[0], 1)
            con.commit()
            with patch('enrich.fragments', return_value=['Texto dos']), patch('enrich.encode', return_value=[vector]):
                embed_batch(con, run, -1)
            self.assertEqual(con.execute('SELECT count(*) FROM semantic_passages p JOIN search_chunks c ON c.id=p.chunk_id WHERE p.source_hash=md5(c.content) AND c.id=%s', (chunk,)).fetchone()[0], 1)
            # Mantener aislada la prueba del worker, que cuenta artículos y trabajos.
            con.execute('DELETE FROM crawl_items WHERE crawl_run_id=%s', (run,))
            con.execute('DELETE FROM crawl_runs WHERE id=%s', (run,))
            con.execute('DELETE FROM norms WHERE id=%s', (norm,))
