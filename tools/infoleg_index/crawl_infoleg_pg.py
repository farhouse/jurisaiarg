#!/usr/bin/env python3
"""Infoleg crawler (PostgreSQL).

MVP:
- Crawls a sequential id range
- Upserts minimal norm fields (title/sumario)
- Stores raw HTML snapshot + status

Usage:
  python3 crawl_infoleg_pg.py \
    --dsn postgresql://user:pass@localhost:5432/juris_ai \
    --start 1 --end 1000
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import random
import re
import time
import urllib.request
from html import unescape

try:
    import psycopg
except Exception:  # pragma: no cover
    psycopg = None

BASE_URL = "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id={id}"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fetch(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "juris-ai-infoleg-pg/0.1"
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        status = getattr(r, "status", 200)
        body = r.read().decode("utf-8", "ignore")
        return status, body


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_title(html: str) -> str | None:
    m = re.search(r"<h\d[^>]*>(.*?)</h\d>", html, re.I | re.S)
    if m:
        return normalize_ws(unescape(strip_tags(m.group(1))))
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if m:
        t = normalize_ws(unescape(strip_tags(m.group(1))))
        return t if t else None
    return None


def parse_sumario(html: str) -> str | None:
    m = re.search(r"Sumario\s*</[^>]+>\s*</td>\s*<td[^>]*>(.*?)</td>", html, re.I | re.S)
    if m:
        s = normalize_ws(unescape(strip_tags(m.group(1))))
        return s if s else None
    return None


def ensure_source(cur):
    cur.execute(
        """
        INSERT INTO sources(code, name, base_url)
        VALUES ('infoleg', 'Infoleg', %s)
        ON CONFLICT(code) DO UPDATE SET base_url=EXCLUDED.base_url
        RETURNING id
        """,
        (BASE_URL.format(id="{id}"),),
    )
    return cur.fetchone()[0]


def create_run(cur, source_id: int, start_id: int, end_id: int) -> int:
    cur.execute(
        """
        INSERT INTO crawl_runs(source_id, start_id, end_id, status)
        VALUES (%s, %s, %s, 'running')
        RETURNING id
        """,
        (source_id, start_id, end_id),
    )
    return cur.fetchone()[0]


def finish_run(cur, run_id: int, status: str, notes: str | None = None):
    cur.execute(
        """
        UPDATE crawl_runs
        SET finished_at = now(), status = %s, notes = %s
        WHERE id = %s
        """,
        (status, notes, run_id),
    )


def upsert_norm(cur, source_id: int, infoleg_id: int, url: str, title: str | None, sumario: str | None) -> int:
    cur.execute(
        """
        INSERT INTO norms(infoleg_id, source_id, url, titulo, sumario, first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, now(), now())
        ON CONFLICT(infoleg_id)
        DO UPDATE SET
          url = EXCLUDED.url,
          titulo = EXCLUDED.titulo,
          sumario = EXCLUDED.sumario,
          last_seen_at = now()
        RETURNING id
        """,
        (infoleg_id, source_id, url, title, sumario),
    )
    return cur.fetchone()[0]


def insert_raw(cur, norm_id: int, run_id: int, status: int | None, html: str | None, error: str | None):
    sha = hashlib.sha256((html or "").encode("utf-8", "ignore")).hexdigest() if html else None
    cur.execute(
        """
        INSERT INTO raw_documents(norm_id, crawl_run_id, http_status, html_raw, sha256, fetched_at, error)
        VALUES (%s, %s, %s, %s, %s, now(), %s)
        """,
        (norm_id, run_id, status, html, sha, error),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True, help="PostgreSQL DSN")
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--jitter", type=float, default=0.1)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--commit-every", type=int, default=50)
    args = ap.parse_args()

    if psycopg is None:
        raise RuntimeError("psycopg no está instalado. Ejecutá: pip install psycopg[binary]")

    with psycopg.connect(args.dsn) as con:
        with con.cursor() as cur:
            source_id = ensure_source(cur)
            run_id = create_run(cur, source_id, args.start, args.end)
        con.commit()

        processed = 0
        try:
            for infoleg_id in range(args.start, args.end + 1):
                url = BASE_URL.format(id=infoleg_id)
                status = None
                html = None
                error = None

                try:
                    status, html = fetch(url, timeout=args.timeout)
                    title = parse_title(html)
                    sumario = parse_sumario(html)
                except Exception as e:  # noqa: BLE001
                    title = None
                    sumario = None
                    error = f"{type(e).__name__}: {e}"

                with con.cursor() as cur:
                    norm_id = upsert_norm(cur, source_id, infoleg_id, url, title, sumario)
                    insert_raw(cur, norm_id, run_id, status, html, error)

                processed += 1
                if processed % args.commit_every == 0:
                    con.commit()
                    print(f"{now_iso()} committed rows={processed} last_id={infoleg_id}")

                time.sleep(max(0.0, args.sleep + random.uniform(-args.jitter, args.jitter)))

            with con.cursor() as cur:
                finish_run(cur, run_id, "ok", notes=f"processed={processed}")
            con.commit()
            print("done")
        except Exception as e:  # noqa: BLE001
            with con.cursor() as cur:
                finish_run(cur, run_id, "error", notes=str(e))
            con.commit()
            raise


if __name__ == "__main__":
    main()
