"""Cola PostgreSQL: un trabajador, sin servidor HTTP ni acceso a Docker."""
import argparse
from pathlib import Path
import signal
import time

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


def initialize(dsn):
    with psycopg.connect(dsn) as con:
        for name in ('schema.sql', 'review_tasks.sql', 'crawl_jobs.sql'):
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
    run_id = con.execute("""INSERT INTO crawl_runs(source_id,requested_ids)
        SELECT id,%s FROM sources WHERE code='infoleg' RETURNING id""", (ids,)).fetchone()[0]
    con.execute("""UPDATE crawl_jobs SET status='running', stage='download',
        started_at=now(), crawl_run_id=%s WHERE id=%s""", (run_id, job['id']))
    con.commit()
    try:
        crawl_ids(con, run_id, ids)
        con.execute("UPDATE crawl_jobs SET stage='parser' WHERE id=%s", (job['id'],))
        con.commit()
        parse_changes(['--dsn', dsn, '--infoleg-ids', *map(str, ids)])
        con.execute("UPDATE crawl_jobs SET stage='review' WHERE id=%s", (job['id'],))
        con.commit()
        prepare_review(['--dsn', dsn, '--run-id', str(run_id), '--indices-only'])
        status = con.execute("SELECT status FROM crawl_runs WHERE id=%s", (run_id,)).fetchone()[0]
        con.execute("UPDATE crawl_jobs SET status=%s, stage='finished', finished_at=now() WHERE id=%s",
                    ('partial' if status == 'partial' else 'done', job['id']))
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


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--init', action='store_true')
    args = cli.parse_args()
    dsn = database_dsn()
    if args.init:
        initialize(dsn)
        return
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    with psycopg.connect(dsn) as con:
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
