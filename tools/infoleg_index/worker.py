"""Cola PostgreSQL: un trabajador, sin servidor HTTP ni acceso a Docker."""
import argparse
from pathlib import Path
import signal
import socket
import time
import subprocess
import sys

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import os

from crawl_infoleg_pg import crawl_ids
from extract_legal_changes import main as parse_changes
from prepare_review import main as prepare_review


def database_dsn():
    return make_conninfo(**{key: os.environ[name] for key, name in {
        'host': 'POSTGRES_HOST', 'port': 'POSTGRES_PORT', 'dbname': 'POSTGRES_DB',
        'user': 'POSTGRES_USER', 'password': 'POSTGRES_PASSWORD',
    }.items()})


def worker_name():
    return f'jurisai-worker:{socket.gethostname()}'


def check_health(dsn):
    # Consultar el lock; intentar adquirirlo desde el chequeo competiría con el worker.
    # El nombre por contenedor evita dar por sano a uno porque otro posee el lock.
    with psycopg.connect(dsn, connect_timeout=3, options='-c statement_timeout=3000', autocommit=True) as con:
        return con.execute("""SELECT EXISTS (
            SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
            WHERE l.locktype='advisory' AND l.classid=72419 AND l.objid=1
              AND l.objsubid=2 AND l.granted
              AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database())
              AND a.application_name=%s
        )""", (worker_name(),)).fetchone()[0]


def initialize(dsn):
    with psycopg.connect(dsn) as con:
        for name in ('schema.sql', 'review_tasks.sql', 'crawl_jobs.sql', 'enrichment.sql'):
            con.execute(Path(__file__).with_name(name).read_text())


def recover_interrupted(con):
    # Solo después de obtener el lock: ningún otro worker está ejecutando estos trabajos.
    con.execute("""UPDATE crawl_runs SET status='error', finished_at=now()
        WHERE status='running' AND id IN (SELECT crawl_run_id FROM crawl_jobs WHERE status='running')""")
    con.execute("""UPDATE crawl_jobs SET status='failed', finished_at=now(),
        error='El trabajador se interrumpió. Podés volver a solicitar el rango; los datos guardados se conservan.'
        WHERE status='running'""")
    con.commit()


def process_next(con, dsn):
    with con.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM crawl_jobs WHERE status='queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
        job = cur.fetchone()
    if not job:
        con.commit()
        return False
    ids = list(range(job['start_id'], job['end_id'] + 1))
    run_id = job['crawl_run_id'] if job['mode'] == 'process' else con.execute("""INSERT INTO crawl_runs(source_id,requested_ids)
        SELECT id,%s FROM sources WHERE code='infoleg' RETURNING id""", (ids,)).fetchone()[0]
    con.execute("""UPDATE crawl_jobs SET status='running',
        started_at=now(), crawl_run_id=%s WHERE id=%s""", (run_id, job['id']))
    con.commit()
    try:
        if job['mode'] != 'process':
            con.execute("UPDATE crawl_jobs SET stage='download' WHERE id=%s", (job['id'],))
            con.commit()
            crawl_ids(con, run_id, ids)
        else:
            row = con.execute("SELECT requested_ids FROM crawl_runs WHERE id=%s AND finished_at IS NOT NULL AND status IN ('ok','partial')", (run_id,)).fetchone()
            if not row:
                raise ValueError('El lote debe haber terminado la descarga')
            ids = row[0]
        con.execute("UPDATE crawl_jobs SET stage='parser' WHERE id=%s", (job['id'],))
        con.commit()
        parse_changes(['--dsn', dsn, '--infoleg-ids', *map(str, ids)])
        if job['mode'] != 'download':
            # Cada proceso termina antes de cargar el siguiente modelo y libera su RAM.
            for stage in ('classify', 'embeddings'):
                con.execute("UPDATE crawl_jobs SET stage=%s WHERE id=%s", (stage, job['id']))
                con.commit()
                run_stage(stage, job['id'], run_id, dsn)
        con.execute("UPDATE crawl_jobs SET stage='review' WHERE id=%s", (job['id'],))
        con.commit()
        prepare_review(['--dsn', dsn, '--run-id', str(run_id), '--indices-only'])
        status = con.execute("SELECT status FROM crawl_runs WHERE id=%s", (run_id,)).fetchone()[0]
        con.execute("UPDATE crawl_jobs SET status=%s, stage='finished', finished_at=now() WHERE id=%s",
                    ('done' if status == 'ok' else 'partial', job['id']))
        con.commit()
    except BaseException as exc:
        con.rollback()
        # No guardar DSN, HTML remoto ni trazas que puedan incluir credenciales.
        error = f"Falló la etapa del trabajo ({type(exc).__name__}). Los datos guardados se conservan; consultá los registros del trabajador."
        con.execute("UPDATE crawl_jobs SET status='failed', error=%s, finished_at=now() WHERE id=%s", (error, job['id']))
        con.execute("UPDATE crawl_runs SET status='error', finished_at=now() WHERE id=%s AND status='running'", (run_id,))
        con.commit()
        print(f"Trabajo {job['id']}: {type(exc).__name__}", flush=True)
        if not isinstance(exc, Exception):
            raise
    return True


def run_stage(stage, job_id, run_id, dsn):
    env = {**os.environ, 'PIPELINE_DSN': dsn}
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('enrich.py')),
                              stage, str(run_id), str(job_id)], env=env)
    try:
        if child.wait() != 0:
            raise RuntimeError(f'Falló {stage}')
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument('--init', action='store_true')
    mode.add_argument('--healthcheck', action='store_true')
    args = cli.parse_args()
    dsn = database_dsn()
    if args.healthcheck:
        try:
            healthy = check_health(dsn)
        except psycopg.Error:
            healthy = False
        if not healthy:
            print('Trabajador sin conexión activa y bloqueo propio de la cola.', flush=True)
        raise SystemExit(0 if healthy else 1)
    if args.init:
        initialize(dsn)
        return
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    with psycopg.connect(dsn, application_name=worker_name()) as con:
        # ponytail: lock global; separar por proveedor si aparecen otras fuentes.
        if not con.execute('SELECT pg_try_advisory_lock(72419, 1)').fetchone()[0]:
            raise SystemExit('Ya hay otro trabajador activo.')
        con.commit()
        recover_interrupted(con)
        print('Trabajador listo; esperando rangos del administrador.', flush=True)
        while True:
            if not process_next(con, dsn):
                time.sleep(3)


if __name__ == '__main__':
    main()
