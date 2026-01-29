#!/usr/bin/env python3
"""Best-effort Infoleg crawler.

Builds a local SQLite index of Infoleg norma pages by sequential id.

MVP extraction:
- titulo (first heading)
- sumario (if present)

Later:
- tipo/numero/fecha/organismo
- modificatorias
"""

import argparse
import datetime as dt
import json
import random
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.parse
from html import unescape

BASE_URL = "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id={id}"


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fetch(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "juris-ai-infoleg-index/0.1 (+https://example.invalid)"
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
    # Try first heading
    m = re.search(r"<h\d[^>]*>(.*?)</h\d>", html, re.I | re.S)
    if m:
        return normalize_ws(unescape(strip_tags(m.group(1))))
    # Fallback: title tag
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if m:
        t = normalize_ws(unescape(strip_tags(m.group(1))))
        return t if t else None
    return None


def parse_sumario(html: str) -> str | None:
    # Heuristic: look for a table row with "Sumario"
    m = re.search(r"Sumario\s*</[^>]+>\s*</td>\s*<td[^>]*>(.*?)</td>", html, re.I | re.S)
    if m:
        s = normalize_ws(unescape(strip_tags(m.group(1))))
        return s if s else None
    return None


def looks_empty(title: str | None, sumario: str | None) -> bool:
    # Some pages show generic placeholders.
    bad_titles = {
        None,
        "",
        "InfoLeg - Información Legislativa",
        "InfoLeg - Informacin Legislativa",
        "SINTETIZADO",
    }
    if title in bad_titles and not sumario:
        return True
    return False


def init_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS normas (
          infoleg_id INTEGER PRIMARY KEY,
          url TEXT NOT NULL,
          http_status INTEGER,
          titulo TEXT,
          sumario TEXT,
          fetched_at TEXT NOT NULL,
          error TEXT
        );
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_normas_titulo ON normas(titulo);"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_normas_sumario ON normas(sumario);"
    )
    con.commit()
    return con


def upsert(con: sqlite3.Connection, row: dict):
    con.execute(
        """
        INSERT INTO normas(infoleg_id, url, http_status, titulo, sumario, fetched_at, error)
        VALUES(:infoleg_id, :url, :http_status, :titulo, :sumario, :fetched_at, :error)
        ON CONFLICT(infoleg_id) DO UPDATE SET
          url=excluded.url,
          http_status=excluded.http_status,
          titulo=excluded.titulo,
          sumario=excluded.sumario,
          fetched_at=excluded.fetched_at,
          error=excluded.error;
        """,
        row,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to sqlite db")
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--sleep", type=float, default=0.2, help="Base sleep seconds between requests")
    ap.add_argument("--jitter", type=float, default=0.1, help="Random jitter added to sleep")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--commit-every", type=int, default=50)
    args = ap.parse_args()

    con = init_db(args.db)

    n = 0
    for infoleg_id in range(args.start, args.end + 1):
        url = BASE_URL.format(id=infoleg_id)
        fetched_at = now_iso()
        row = {
            "infoleg_id": infoleg_id,
            "url": url,
            "http_status": None,
            "titulo": None,
            "sumario": None,
            "fetched_at": fetched_at,
            "error": None,
        }

        try:
            status, html = fetch(url, timeout=args.timeout)
            row["http_status"] = status
            title = parse_title(html)
            sumario = parse_sumario(html)
            row["titulo"] = title
            row["sumario"] = sumario

            # If it looks empty, still store; future parsers might extract more.
            if looks_empty(title, sumario):
                pass

        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"

        upsert(con, row)
        n += 1
        if n % args.commit_every == 0:
            con.commit()
            print(f"{infoleg_id} committed ({n} rows)")

        time.sleep(max(0.0, args.sleep + random.uniform(-args.jitter, args.jitter)))

    con.commit()
    print("done")


if __name__ == "__main__":
    main()
