"""Control de cola sobre una BD test_ descartable, sin red ni inferencia."""
import os
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

import worker


@unittest.skipUnless(os.getenv('CRAWLER_TEST_DSN'), 'requiere BD test_ aislada')
class JobControlTest(unittest.TestCase):
    def setUp(self):
        self.dsn=os.environ['CRAWLER_TEST_DSN']
        self.assertTrue(conninfo_to_dict(self.dsn)['dbname'].startswith('test_'))
        worker.initialize(self.dsn)

    def test_running_child_is_terminated_and_legacy_duplicates_do_not_execute(self):
        with psycopg.connect(self.dsn) as con:
            run=con.execute("INSERT INTO crawl_runs(source_id,status,finished_at,requested_ids) SELECT id,'ok',now(),'{1}' FROM sources WHERE code='infoleg' RETURNING id").fetchone()[0]
            norm=con.execute("INSERT INTO norms(infoleg_id) VALUES(1) RETURNING id").fetchone()[0]
            def analysis(stages):
                job=con.execute("INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by,mode,crawl_run_id) VALUES(%s,1,1,'test','pilot',%s) RETURNING id",(uuid.uuid4(),run)).fetchone()[0]
                pilot=con.execute("INSERT INTO analysis_pilots(job_id,crawl_run_id,model,status,phase,config) VALUES(%s,%s,'test:free','running','analyze',%s) RETURNING id",(job,run,Jsonb({'stages':stages,'group_pilot_id':100}))).fetchone()[0]
                con.execute("INSERT INTO analysis_pilot_items(pilot_id,norm_id,snapshot,split,fingerprint,result,laya) VALUES(%s,%s,'{}','development','fixture','{\"saved\":true}','{\"saved\":true}')",(pilot,norm))
                return job,pilot
            job,pilot=analysis(['laya'])
            duplicate,duplicate_pilot=analysis(['laya'])
            separate,_=analysis(['llm'])
            con.commit()
            real_popen=subprocess.Popen
            children=[]
            def launch(args,**kwargs):
                self.assertEqual(args[2],'laya')
                child=real_popen([sys.executable,'-c','import time; time.sleep(120)'])
                children.append(child)
                with psycopg.connect(self.dsn,autocommit=True) as other:
                    other.execute('UPDATE crawl_jobs SET cancel_requested_at=now() WHERE id=%s',(job,))
                return child
            with patch('worker.subprocess.Popen',side_effect=launch):
                self.assertTrue(worker.process_pilot(con,self.dsn,{'id':job}))
            self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
            self.assertEqual(con.execute('SELECT status,stage FROM crawl_jobs WHERE id=%s',(job,)).fetchone(),('cancelled','cancelled'))
            self.assertEqual(con.execute('SELECT status FROM analysis_pilots WHERE id=%s',(pilot,)).fetchone()[0],'cancelled')
            self.assertEqual(con.execute('SELECT result,laya FROM analysis_pilot_items WHERE pilot_id=%s',(pilot,)).fetchone(),({'saved':True},{'saved':True}))
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s',(duplicate,)).fetchone()[0],'failed')
            self.assertEqual(con.execute('SELECT status FROM analysis_pilots WHERE id=%s',(duplicate_pilot,)).fetchone()[0],'waiting')
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s',(separate,)).fetchone()[0],'queued')
            con.execute("UPDATE crawl_jobs SET status='running',cancel_requested_at=now() WHERE id=%s",(separate,));con.commit()
            worker.recover_interrupted(con)
            self.assertEqual(con.execute('SELECT status FROM crawl_jobs WHERE id=%s',(separate,)).fetchone()[0],'cancelled')

    def test_download_stops_between_documents_without_starting_models(self):
        with psycopg.connect(self.dsn) as con:
            job=con.execute("INSERT INTO crawl_jobs(submission_id,start_id,end_id,requested_by) VALUES(%s,10,20,'test') RETURNING id",(uuid.uuid4(),)).fetchone()[0]
            con.commit()
            def crawl(con,run,ids,report):
                with psycopg.connect(self.dsn,autocommit=True) as other:
                    other.execute('UPDATE crawl_jobs SET cancel_requested_at=now() WHERE id=%s',(job,))
                report('Siguiente documento')
            with patch('worker.crawl_ids',side_effect=crawl),patch('worker.parse_changes') as parser,patch('worker.run_stage') as models:
                self.assertTrue(worker.process_next(con,self.dsn))
            parser.assert_not_called();models.assert_not_called()
            status,run=con.execute('SELECT status,crawl_run_id FROM crawl_jobs WHERE id=%s',(job,)).fetchone()
            self.assertEqual(status,'cancelled')
            self.assertEqual(con.execute('SELECT status FROM crawl_runs WHERE id=%s',(run,)).fetchone()[0],'partial')


if __name__=='__main__':
    unittest.main()
