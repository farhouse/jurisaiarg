"""Actividad confirmada y señal de conexión independientes para la cola local."""
import threading

import psycopg
from psycopg.types.json import Jsonb


class JobCancelled(Exception):
    """Detención solicitada por el administrador, distinta de un error."""


def lock_job(con, job_id):
    # Mismo orden que Gestión: lote, piloto y trabajo. Evita invertir locks al finalizar/detener.
    con.execute('SELECT id FROM crawl_runs WHERE id=(SELECT crawl_run_id FROM crawl_jobs WHERE id=%s) FOR UPDATE',(job_id,)).fetchall()
    con.execute('SELECT id FROM analysis_pilots WHERE job_id=%s FOR UPDATE',(job_id,)).fetchall()
    return con.execute('SELECT cancel_requested_at FROM crawl_jobs WHERE id=%s FOR UPDATE',(job_id,)).fetchone()


def check_cancelled(con, job_id, lock=False):
    row = lock_job(con,job_id) if lock else con.execute('SELECT cancel_requested_at FROM crawl_jobs WHERE id=%s',(job_id,)).fetchone()
    if row and row[0]:
        raise JobCancelled()


class JobActivity:
    def __init__(self, dsn, job_id, heartbeat=False):
        self.dsn, self.job_id, self.heartbeat = dsn, job_id, heartbeat
        self.stop = threading.Event()

    def __enter__(self):
        self.con = psycopg.connect(self.dsn, autocommit=True, connect_timeout=3,
                                   options='-c statement_timeout=3000')
        if self.heartbeat:
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()
        return self

    def __call__(self, message, **details):
        check_cancelled(self.con,self.job_id)
        self.con.execute('''UPDATE crawl_jobs SET last_activity_at=clock_timestamp(),
            progress=progress || %s WHERE id=%s AND status='running' ''',
            (Jsonb({'activity': {'message': message, **details}}), self.job_id))

    def _heartbeat(self):
        # Conexión propia: una inferencia o transacción larga no bloquea esta señal.
        # Solo prueba que el supervisor puede comunicarse, no que el modelo avanza.
        while not self.stop.is_set():
            try:
                with psycopg.connect(self.dsn, autocommit=True, connect_timeout=3,
                                     options='-c statement_timeout=3000') as con:
                    con.execute("UPDATE crawl_jobs SET heartbeat_at=clock_timestamp() WHERE id=%s AND status='running'",
                                (self.job_id,))
            except psycopg.Error:
                pass  # La señal vence en la página; reintentar sin ocultar el retraso.
            self.stop.wait(10)

    def __exit__(self, *_):
        self.stop.set()
        if self.heartbeat:
            self.thread.join(timeout=7)
        self.con.close()


def prepare_model(repo, revision, files, label, report):
    """Manifiestos de las revisiones fijadas; solo descargar archivos que faltan."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    report(f'Comprobando archivos de {label}', model=label)
    missing = []
    for filename in files:
        try:
            hf_hub_download(repo, filename, revision=revision, local_files_only=True)
        except LocalEntryNotFoundError:
            missing.append(filename)
    for done, filename in enumerate(missing):
        report(f'Descargando {label}', model=label, file=filename,
               files_done=done, files_total=len(missing))
        hf_hub_download(repo, filename, revision=revision)
        report(f'Descargando {label}', model=label, file=filename,
               files_done=done + 1, files_total=len(missing))
    report(f'Cargando {label} en memoria', model=label, cached=not missing)
