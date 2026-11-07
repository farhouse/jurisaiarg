#!/usr/bin/env python3
"""Extrae cambios jurídicos precisos desde notas editoriales de InfoLEG."""

from __future__ import annotations

import argparse

import psycopg

from legal_parser import (
    PARSER_VERSION,
    extract_legal_changes,
    fingerprint,
    normalize_norm_number,
    normalize_norm_type,
)


def load_norm_index(cur) -> dict[tuple[str, str], list[int]]:
    cur.execute("SELECT id, norm_type, number FROM norms WHERE norm_type IS NOT NULL AND number IS NOT NULL")
    index: dict[tuple[str, str], list[int]] = {}
    for norm_id, norm_type, number in cur.fetchall():
        key = (normalize_norm_type(norm_type), normalize_norm_number(number))
        index.setdefault(key, []).append(norm_id)
    return index


def main(argv=None, report=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--infoleg-ids", type=int, nargs="*")
    args = parser.parse_args(argv)

    with psycopg.connect(args.dsn) as con:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO legal_parse_runs(parser_version) VALUES (%s) RETURNING id",
                (PARSER_VERSION,),
            )
            run_id = cur.fetchone()[0]
            norm_index = load_norm_index(cur)
            query = """
                SELECT av.id, a.id, a.article_key, n.id, n.infoleg_id, av.text_normalized
                FROM article_versions av
                JOIN articles a ON a.id=av.article_id
                JOIN norm_versions nv ON nv.id=av.norm_version_id
                JOIN norms n ON n.id=nv.norm_id
                WHERE nv.version_type='actualizada'
            """
            params = []
            if args.infoleg_ids:
                query += " AND n.infoleg_id = ANY(%s)"
                params.append(args.infoleg_ids)
            query += " ORDER BY n.infoleg_id, a.ordinal"
            cur.execute(query, params)
            rows = cur.fetchall()
        con.commit()

        detected = 0
        resolved = 0
        try:
            for article_version_id, article_id, article_key, affected_norm_id, infoleg_id, text in rows:
                if report:
                    report('Extrayendo relaciones con el parser', document=f'InfoLEG {infoleg_id} · artículo {article_key}')
                for candidate in extract_legal_changes(text):
                    matches = norm_index.get((
                        normalize_norm_type(candidate.amending_norm_type),
                        normalize_norm_number(candidate.amending_norm_number),
                    ), [])
                    amending_norm_id = matches[0] if len(matches) == 1 else None
                    confidence = 0.95 if amending_norm_id else 0.78
                    if candidate.affected_scope != "articulo":
                        confidence -= 0.05
                    change_fingerprint = fingerprint(infoleg_id, article_key, candidate)
                    with con.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO legal_changes(
                              parse_run_id, amending_norm_id, affected_norm_id,
                              affected_article_id, source_article_version_id,
                              amending_reference_raw, amending_article_reference,
                              change_type, affected_scope, publication_date, effect_date,
                              evidence_text, parser_version, confidence, fingerprint)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(fingerprint) DO UPDATE SET
                              parse_run_id=EXCLUDED.parse_run_id,
                              amending_norm_id=EXCLUDED.amending_norm_id,
                              publication_date=EXCLUDED.publication_date,
                              effect_date=EXCLUDED.effect_date,
                              confidence=EXCLUDED.confidence,
                              updated_at=now()
                            """,
                            (run_id, amending_norm_id, affected_norm_id, article_id,
                             article_version_id, candidate.amending_reference_raw,
                             candidate.amending_article_reference, candidate.change_type,
                             candidate.affected_scope, candidate.publication_date,
                             candidate.effect_date, candidate.evidence_text,
                             PARSER_VERSION, confidence, change_fingerprint),
                        )
                    detected += 1
                    resolved += int(amending_norm_id is not None)
            with con.cursor() as cur:
                cur.execute(
                    """UPDATE legal_parse_runs SET status='ok', finished_at=now(),
                         scanned_articles=%s, detected_changes=%s, resolved_norms=%s
                         WHERE id=%s""",
                    (len(rows), detected, resolved, run_id),
                )
            con.commit()
            print(f"run={run_id} scanned={len(rows)} detected={detected} resolved={resolved}")
        except Exception as exc:
            con.rollback()
            with con.cursor() as cur:
                cur.execute(
                    """UPDATE legal_parse_runs SET status='error', finished_at=now(),
                         scanned_articles=%s, detected_changes=%s, resolved_norms=%s,
                         notes=%s WHERE id=%s""",
                    (len(rows), detected, resolved, str(exc), run_id),
                )
            con.commit()
            raise


if __name__ == "__main__":
    main()
