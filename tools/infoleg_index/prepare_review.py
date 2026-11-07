#!/usr/bin/env python3
"""Prepara la cola de un lote con parser + Laya, sin sobrescribir revisiones."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from legal_parser import NOTE_BLOCK, extract_legal_changes, fingerprint
from try_laya import MODEL_REVISION, TRIAGE_VERSION, classify


def passages(text, width=1800, overlap=180):
    """Notas completas y texto restante; ventanas solapadas si el artículo es largo."""
    notes = list(NOTE_BLOCK.finditer(text))
    for match in notes:
        yield match.group(0), False
    remainder = NOTE_BLOCK.sub(" ", text).strip()
    # ponytail: ventanas por caracteres; conservar contexto y revisar divisiones
    # hasta que una segmentación jurídica más fina esté evaluada.
    for start in range(0, len(remainder), width - overlap):
        fragment = remainder[start:start + width].strip()
        if fragment:
            yield fragment, len(remainder) > width
        if start + width >= len(remainder):
            break


def save_task(con, run_id, norm_id, kind, text, result, article_id=None, change_id=None, key=None):
    con.execute(
        """INSERT INTO review_tasks(crawl_run_id,norm_id,kind,source_text,decision,
             prediction,probability,reasons,model_result,article_version_id,legal_change_id,fingerprint)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fingerprint) DO NOTHING""",
        (run_id, norm_id, kind, text, result["decision"], result.get("prediction"),
         result.get("predicted_probability"), Jsonb(result["review_reasons"]), Jsonb(result),
         article_id, change_id, key),
    )
    con.commit()


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--dsn", required=True)
    cli.add_argument("--run-id", required=True, type=int)
    cli.add_argument("--indices-only", action="store_true", help="Preparar controles de índice sin cargar Laya")
    args = cli.parse_args(argv)
    os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent / ".laya-cache"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    counts = Counter()
    with psycopg.connect(args.dsn) as con:
        if not con.execute("SELECT 1 FROM crawl_runs WHERE id=%s AND finished_at IS NOT NULL", (args.run_id,)).fetchone():
            cli.error("El lote no existe o aún está en curso")
        items = con.execute("""SELECT n.id,n.infoleg_id,n.canonical_title,n.subject,n.article_count,ci.status
            FROM crawl_items ci JOIN norms n ON n.id=ci.norm_id WHERE ci.crawl_run_id=%s ORDER BY ci.infoleg_id""", (args.run_id,)).fetchall()
        for norm_id, infoleg_id, title, subject, article_count, status in items:
            text = f"InfoLEG {infoleg_id}\n{title or subject or 'Sin título'}\nEstado de descarga: {status}\nArtículos disponibles: {article_count}"
            save_task(con, args.run_id, norm_id, "indice", text,
                      {"decision": "revision", "review_reasons": ["control_del_indice"]},
                      key=f"index:{args.run_id}:{norm_id}")
        if args.indices_only:
            print(f"indices={len(items)}; Laya pendiente", flush=True)
            return
        from laya import Router
        router = Router(device="cpu", max_loaded=1, revision=MODEL_REVISION)
        rows = con.execute("""WITH selected AS (
                SELECT DISTINCT ON (nv.norm_id) nv.id,nv.norm_id FROM norm_versions nv
                WHERE nv.norm_id IN (SELECT norm_id FROM crawl_items WHERE crawl_run_id=%s)
                ORDER BY nv.norm_id,(nv.version_type='actualizada') DESC,nv.id DESC
            ) SELECT av.id,s.norm_id,n.infoleg_id,a.article_key,av.text_normalized
              FROM selected s JOIN article_versions av ON av.norm_version_id=s.id
              JOIN articles a ON a.id=av.article_id JOIN norms n ON n.id=s.norm_id
              ORDER BY n.infoleg_id,a.ordinal,av.id""", (args.run_id,)).fetchall()
        con.commit()
        for av_id, norm_id, infoleg_id, article_key, text in rows:
            for fragment, divided in passages(text):
                key = hashlib.sha256(f"{args.run_id}|{av_id}|{MODEL_REVISION}|{TRIAGE_VERSION}|0.75|{fragment}".encode()).hexdigest()
                if con.execute("SELECT 1 FROM review_tasks WHERE fingerprint=%s", (key,)).fetchone():
                    con.commit()
                    counts["already_present"] += 1
                    continue
                con.commit()
                candidates = extract_legal_changes(fragment)
                change_id = None
                resolved = False
                if len(candidates) == 1:
                    change = con.execute("SELECT id,amending_norm_id FROM legal_changes WHERE fingerprint=%s",
                                         (fingerprint(infoleg_id, article_key, candidates[0]),)).fetchone()
                    if change:
                        change_id, resolved = change[0], bool(change[1])
                con.commit()
                try:
                    result = classify(router, {"text": fragment, "infoleg_id": infoleg_id})
                except Exception as exc:
                    result = {"decision": "revision", "review_reasons": ["error_modelo"], "error": str(exc)}
                if candidates and not resolved:
                    result["review_reasons"].append("norma_sin_resolver")
                if divided:
                    result["review_reasons"].append("fragmento_dividido")
                if result["review_reasons"]:
                    result["decision"] = "revision"
                result["requires_review"] = result["decision"] == "revision"
                result.update(model_revision=MODEL_REVISION, triage_version=TRIAGE_VERSION, min_probability=0.75)
                save_task(con, args.run_id, norm_id, "pasaje", fragment, result, av_id, change_id, key)
                counts[result["decision"]] += 1
            print(f"InfoLEG {infoleg_id} art. {article_key}: {dict(counts)}", flush=True)
        print(json.dumps({"indices": len(items), "articles": len(rows), **counts}), flush=True)
        con.execute("UPDATE crawl_runs SET review_prepared_at=now() WHERE id=%s", (args.run_id,))
        con.commit()


if __name__ == "__main__":
    main()
