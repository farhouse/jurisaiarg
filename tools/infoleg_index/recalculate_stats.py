#!/usr/bin/env python3
"""Recalcula estados e indicadores de todas las normas existentes."""

import argparse

import psycopg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()

    with psycopg.connect(args.dsn) as con:
        with con.cursor() as cur:
            cur.execute(
                """
                WITH stats AS (
                  SELECT n.id,
                    EXISTS (SELECT 1 FROM norm_versions v
                            WHERE v.norm_id=n.id AND v.version_type='original') has_original,
                    EXISTS (SELECT 1 FROM norm_versions v
                            WHERE v.norm_id=n.id AND v.version_type='actualizada') has_current,
                    EXISTS (SELECT 1 FROM norm_relations r
                            WHERE r.source_norm_id=n.id) has_outgoing,
                    EXISTS (SELECT 1 FROM norm_relations r
                            WHERE r.target_norm_id=n.id) has_incoming,
                    (SELECT count(*)::int FROM articles a WHERE a.norm_id=n.id) articles
                  FROM norms n
                )
                UPDATE norms n SET
                  has_original_text=s.has_original,
                  has_current_text=s.has_current,
                  has_outgoing_relations=s.has_outgoing,
                  has_incoming_relations=s.has_incoming,
                  article_count=s.articles,
                  index_status=CASE
                    WHEN n.norm_type IS NULL AND n.number IS NULL
                      AND n.canonical_title IS NULL AND n.subject IS NULL AND n.summary IS NULL
                      THEN 'vacia'
                    WHEN s.has_current AND s.articles > 0 THEN 'completa'
                    WHEN s.has_original AND s.articles > 0 THEN 'solo_texto_original'
                    ELSE 'solo_ficha'
                  END,
                  updated_at=now()
                FROM stats s WHERE n.id=s.id
                """
            )
            recalculated = cur.rowcount
        con.commit()
        print(f"recalculated={recalculated}")


if __name__ == "__main__":
    main()
