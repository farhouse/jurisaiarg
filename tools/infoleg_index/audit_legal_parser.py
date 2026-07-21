#!/usr/bin/env python3
"""Mide cobertura sintáctica y muestra notas no reconocidas."""

import argparse

import psycopg

from legal_parser import CHANGE_PATTERN, NOTE_BLOCK


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    with psycopg.connect(args.dsn) as con, con.cursor() as cur:
        cur.execute(
            """
            SELECT n.infoleg_id, a.article_label, av.text_normalized
            FROM article_versions av
            JOIN articles a ON a.id=av.article_id
            JOIN norm_versions nv ON nv.id=av.norm_version_id
            JOIN norms n ON n.id=nv.norm_id
            WHERE nv.version_type='actualizada'
            ORDER BY n.infoleg_id, a.ordinal
            """
        )
        rows = cur.fetchall()

    total_notes = 0
    recognized = 0
    unmatched = []
    for infoleg_id, article_label, text in rows:
        for note_match in NOTE_BLOCK.finditer(text or ""):
            total_notes += 1
            note = " ".join(note_match.group(0).split())
            if CHANGE_PATTERN.search(note_match.group("note")):
                recognized += 1
            else:
                unmatched.append((infoleg_id, article_label, note))

    coverage = (100 * recognized / total_notes) if total_notes else 100
    print(
        f"scanned_articles={len(rows)} explicit_notes={total_notes} "
        f"recognized={recognized} unmatched={len(unmatched)} coverage={coverage:.1f}%"
    )
    for infoleg_id, article_label, note in unmatched[:args.limit]:
        print(f"UNMATCHED infoleg_id={infoleg_id} article={article_label}: {note}")


if __name__ == "__main__":
    main()
