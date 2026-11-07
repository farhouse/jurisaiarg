#!/usr/bin/env python3
"""Crawler estructurado de InfoLEG para PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import random
import time
import urllib.error
import urllib.request

import psycopg

from parser import BASE_URL, PARSER_VERSION, parse_articles, parse_card, parse_relation_rows, text_hash


CARD_URL = BASE_URL + "verNorma.do?id={id}"


def decode_html(raw: bytes, charset: str | None) -> str:
    """Decodifica HTML y elimina NUL sólo de la representación textual.

    Los bytes originales se almacenan por separado para preservar la fuente.
    """
    encoding = charset or "windows-1252"
    try:
        text = raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("windows-1252", "replace")
    return text.replace("\x00", "")


def fetch(url: str, timeout: int) -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "juris-ai-infoleg/0.2"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "windows-1252"
        return getattr(response, "status", 200), decode_html(raw, charset), raw


def upsert_norm(cur, infoleg_id: int, **fields) -> int:
    cur.execute(
        """
        INSERT INTO norms(infoleg_id, norm_type, number, canonical_title, issuing_body,
                          subject, summary, updated_at)
        VALUES (%(infoleg_id)s, %(norm_type)s, %(number)s, %(canonical_title)s,
                %(issuing_body)s, %(subject)s, %(summary)s, now())
        ON CONFLICT(infoleg_id) DO UPDATE SET
          norm_type = coalesce(EXCLUDED.norm_type, norms.norm_type),
          number = coalesce(EXCLUDED.number, norms.number),
          canonical_title = coalesce(EXCLUDED.canonical_title, norms.canonical_title),
          issuing_body = coalesce(EXCLUDED.issuing_body, norms.issuing_body),
          subject = coalesce(EXCLUDED.subject, norms.subject),
          summary = coalesce(EXCLUDED.summary, norms.summary),
          updated_at = now()
        RETURNING id
        """,
        {"infoleg_id": infoleg_id, **fields},
    )
    return cur.fetchone()[0]


def add_source(cur, norm_id: int, run_id: int, external_id: int, url: str,
               kind: str, status: int | None, html: str | None,
               parse_status: str = "ok", error: str | None = None,
               raw_bytes: bytes | None = None) -> int:
    digest_source = raw_bytes if raw_bytes is not None else (html.encode("utf-8") if html else None)
    digest = hashlib.sha256(digest_source).hexdigest() if digest_source else None
    cur.execute(
        """
        INSERT INTO norm_sources(norm_id, crawl_run_id, external_id, url,
          document_kind, http_status, content_hash, raw_content_bytes, raw_content, parser_version,
          parse_status, error_detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (norm_id, run_id, str(external_id), url, kind, status, digest, raw_bytes, html,
         PARSER_VERSION, parse_status, error),
    )
    return cur.fetchone()[0]


def add_date(cur, norm_id: int, date_type: str, value):
    if value:
        cur.execute(
            "INSERT INTO norm_dates(norm_id, date_type, date_value) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (norm_id, date_type, value),
        )


def add_version(cur, norm_id: int, source_id: int, version_type: str,
                full_text: str, articles: list[dict], is_current: bool):
    digest = text_hash(full_text)
    cur.execute(
        """
        INSERT INTO norm_versions(norm_id, source_id, version_type, full_text_original,
          full_text_normalized, text_hash, is_current)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(norm_id, version_type, text_hash) DO UPDATE SET is_current=EXCLUDED.is_current
        RETURNING id
        """,
        (norm_id, source_id, version_type, full_text, full_text, digest, is_current),
    )
    version_id = cur.fetchone()[0]
    for article in articles:
        cur.execute(
            """
            INSERT INTO articles(norm_id, article_label, article_key, ordinal)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT(norm_id, article_key) DO UPDATE SET
              article_label=EXCLUDED.article_label, ordinal=EXCLUDED.ordinal
            RETURNING id
            """,
            (norm_id, article["label"], article["key"], article["ordinal"]),
        )
        article_id = cur.fetchone()[0]
        status = "derogado" if "artículo derogado" in article["text"].lower() else "desconocido"
        article_digest = text_hash(article["text"])
        cur.execute(
            """
            INSERT INTO article_versions(article_id, norm_version_id, text_original,
              text_normalized, status, text_hash)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(article_id, norm_version_id) DO UPDATE SET
              text_original=EXCLUDED.text_original,
              text_normalized=EXCLUDED.text_normalized,
              status=EXCLUDED.status,
              text_hash=EXCLUDED.text_hash
            RETURNING id
            """,
            (article_id, version_id, article["text"], article["text"], status, article_digest),
        )
        article_version_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO search_chunks(norm_id, article_version_id, chunk_type, content,
              metadata)
            VALUES (%s,%s,'articulo',%s,
              jsonb_build_object('version_type', %s::text, 'article', %s::text))
            ON CONFLICT(norm_id, article_version_id, chunk_type) DO UPDATE SET content=EXCLUDED.content
            """,
            (norm_id, article_version_id, article["text"], version_type, article["label"]),
        )


def refresh_norm_stats(cur, norm_id: int) -> str:
    """Recalcula indicadores denormalizados y devuelve el estado del índice."""
    cur.execute(
        """
        WITH stats AS (
          SELECT
            EXISTS (SELECT 1 FROM norm_versions v
                    WHERE v.norm_id=%(norm_id)s AND v.version_type='original') AS has_original,
            EXISTS (SELECT 1 FROM norm_versions v
                    WHERE v.norm_id=%(norm_id)s AND v.version_type='actualizada') AS has_current,
            EXISTS (SELECT 1 FROM norm_relations r
                    WHERE r.source_norm_id=%(norm_id)s) AS has_outgoing,
            EXISTS (SELECT 1 FROM norm_relations r
                    WHERE r.target_norm_id=%(norm_id)s) AS has_incoming,
            (SELECT count(*)::int FROM articles a WHERE a.norm_id=%(norm_id)s) AS articles
        )
        UPDATE norms n SET
          has_original_text=stats.has_original,
          has_current_text=stats.has_current,
          has_outgoing_relations=stats.has_outgoing,
          has_incoming_relations=stats.has_incoming,
          article_count=stats.articles,
          index_status=CASE
            WHEN n.norm_type IS NULL AND n.number IS NULL
              AND n.canonical_title IS NULL AND n.subject IS NULL AND n.summary IS NULL
              THEN 'vacia'
            WHEN stats.has_current AND stats.articles > 0 THEN 'completa'
            WHEN stats.has_original AND stats.articles > 0 THEN 'solo_texto_original'
            ELSE 'solo_ficha'
          END,
          updated_at=now()
        FROM stats
        WHERE n.id=%(norm_id)s
        RETURNING n.index_status
        """,
        {"norm_id": norm_id},
    )
    return cur.fetchone()[0]


def crawl_relations(cur, norm_id: int, infoleg_id: int, run_id: int, url: str,
                    kind: str, timeout: int):
    status, html, raw = fetch(url, timeout)
    source_page_id = add_source(cur, norm_id, run_id, infoleg_id, url, kind,
                                status, html, raw_bytes=raw)
    for row in parse_relation_rows(html):
        linked_id = upsert_norm(
            cur,
            row["infoleg_id"],
            norm_type=row["norm_type"], number=row["number"],
            issuing_body=row["issuing_body"], canonical_title=None,
            subject=None, summary=row["description"],
        )
        add_date(cur, linked_id, "publicacion", row["publication_date"])
        if kind == "vinculos_modificada_por":
            source_norm_id, target_norm_id = linked_id, norm_id
        else:
            source_norm_id, target_norm_id = norm_id, linked_id
        cur.execute(
            """
            INSERT INTO norm_relations(source_norm_id, target_norm_id, relation_type,
              evidence_text, extraction_method, confidence, source_page_id)
            VALUES (%s,%s,'modifica_o_complementa',%s,'fuente',0.700,%s)
            ON CONFLICT(source_norm_id, target_norm_id, relation_type) DO UPDATE SET
              evidence_text=EXCLUDED.evidence_text,
              source_page_id=EXCLUDED.source_page_id
            """,
            (source_norm_id, target_norm_id, row["evidence"], source_page_id),
        )
        refresh_norm_stats(cur, linked_id)


def crawl_one(con, run_id: int, infoleg_id: int, timeout: int) -> tuple[int, str]:
    card_url = CARD_URL.format(id=infoleg_id)
    status, card_html, card_raw = fetch(card_url, timeout)
    card = parse_card(card_html, card_url)
    with con.cursor() as cur:
        norm_id = upsert_norm(
            cur, infoleg_id, norm_type=card.norm_type, number=card.number,
            issuing_body=card.issuing_body, canonical_title=card.canonical_title,
            subject=card.subject, summary=card.summary,
        )
        is_empty = not any((card.norm_type, card.number, card.canonical_title, card.subject, card.summary))
        has_text_link = bool(card.original_url or card.current_url)
        card_parse_status = "parcial" if is_empty or not has_text_link else "ok"
        card_error = "EMPTY_CARD" if is_empty else ("NO_TEXT_LINKS" if not has_text_link else None)
        add_source(cur, norm_id, run_id, infoleg_id, card_url, "ficha", status,
                   card_html, card_parse_status, card_error, card_raw)
        add_date(cur, norm_id, "sancion", card.sanction_date)
        add_date(cur, norm_id, "publicacion", card.publication_date)
        cur.execute(
            """INSERT INTO search_chunks(norm_id, chunk_type, content, metadata)
               VALUES (%s,'titulo',%s,'{}')
               ON CONFLICT(norm_id, chunk_type) WHERE article_version_id IS NULL
               DO UPDATE SET content=EXCLUDED.content""",
            (norm_id, " ".join(filter(None, [card.canonical_title, card.subject]))),
        )
        for url, kind, version_type, current in (
            (card.original_url, "texto_original", "original", False),
            (card.current_url, "texto_actualizado", "actualizada", True),
        ):
            if not url:
                continue
            page_status, html, raw = fetch(url, timeout)
            source_page_id = add_source(cur, norm_id, run_id, infoleg_id, url, kind,
                                        page_status, html, raw_bytes=raw)
            full_text, articles = parse_articles(html)
            add_version(cur, norm_id, source_page_id, version_type, full_text, articles, current)
        if card.modifies_url:
            crawl_relations(cur, norm_id, infoleg_id, run_id, card.modifies_url,
                            "vinculos_modifica", timeout)
        if card.modified_by_url:
            crawl_relations(cur, norm_id, infoleg_id, run_id, card.modified_by_url,
                            "vinculos_modificada_por", timeout)
        index_status = refresh_norm_stats(cur, norm_id)
    con.commit()
    return norm_id, index_status


def record_failed_item(con, run_id: int, infoleg_id: int, exc: Exception) -> tuple[int, str]:
    status = "error_descarga" if isinstance(exc, (urllib.error.URLError, TimeoutError)) else "error_parseo"
    card_url = CARD_URL.format(id=infoleg_id)
    with con.cursor() as cur:
        norm_id = upsert_norm(
            cur, infoleg_id, norm_type=None, number=None, issuing_body=None,
            canonical_title=None, subject=None, summary=None,
        )
        add_source(cur, norm_id, run_id, infoleg_id, card_url, "ficha", None,
                   None, "error", f"{type(exc).__name__}: {exc}")
        cur.execute(
            "UPDATE norms SET index_status=%s, updated_at=now() WHERE id=%s",
            (status, norm_id),
        )
    con.commit()
    return norm_id, status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--ids", type=int, nargs="+", required=True)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--jitter", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    with psycopg.connect(args.dsn) as con:
        with con.cursor() as cur:
            cur.execute("SELECT id FROM sources WHERE code='infoleg'")
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO crawl_runs(source_id, requested_ids) VALUES (%s,%s) RETURNING id",
                (source_id, args.ids),
            )
            run_id = cur.fetchone()[0]
        con.commit()
        crawl_ids(con, run_id, args.ids, args.timeout, args.sleep, args.jitter)


def crawl_ids(con, run_id, ids, timeout=30, delay=0.5, jitter=0.2):
    processed = 0
    failures = 0
    for infoleg_id in ids:
        with con.cursor() as cur:
            cur.execute(
                """INSERT INTO crawl_items(crawl_run_id, infoleg_id, status)
                   VALUES (%s,%s,'procesando') RETURNING id""",
                (run_id, infoleg_id),
            )
            item_id = cur.fetchone()[0]
        con.commit()
        error_detail = None
        try:
            norm_id, item_status = crawl_one(con, run_id, infoleg_id, timeout)
        except Exception as exc:  # un ID defectuoso no aborta el lote
            con.rollback()
            norm_id, item_status = record_failed_item(con, run_id, infoleg_id, exc)
            error_detail = f"{type(exc).__name__}: {exc}"
            failures += 1
        with con.cursor() as cur:
            cur.execute(
                """UPDATE crawl_items SET norm_id=%s, status=%s, error_detail=%s,
                     finished_at=now() WHERE id=%s""",
                (norm_id, item_status, error_detail, item_id),
            )
        processed += 1
        con.execute("UPDATE crawl_runs SET processed_count=%s WHERE id=%s", (processed, run_id))
        con.commit()
        time.sleep(max(0, delay + random.uniform(-jitter, jitter)))

    with con.cursor() as cur:
        cur.execute(
            """UPDATE crawl_runs SET status=%s, finished_at=now(), processed_count=%s,
                 notes=%s WHERE id=%s""",
            ("partial" if failures else "ok", processed,
             f"failures={failures}" if failures else None, run_id),
        )
    con.commit()

if __name__ == "__main__":
    main()
