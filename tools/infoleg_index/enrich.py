"""Etapas locales reanudables. Un proceso por modelo; resultados por contenido."""
import hashlib
import os
from pathlib import Path
import sys

import psycopg
from psycopg.types.json import Jsonb

# Mismo módulo en el checkout y en ambas imágenes; sin duplicar el algoritmo.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'web' / 'review') if len(Path(__file__).resolve().parents) > 2 else '/app')
from embedding import (VERSION as EMBEDDING_VERSION, MODEL as EMBEDDING_MODEL,
                       REVISION as EMBEDDING_REVISION, encode, fragments, runtime)
from job_activity import JobActivity, prepare_model
from prepare_review import main as prepare_review, save_task
from topics import VERSION as TOPICS_VERSION, classify_norm_topics
from try_laya import MODEL_REVISION


def chunks(con, run_id):
    return con.execute('''SELECT sc.id,sc.norm_id,sc.article_version_id,sc.content,md5(sc.content)
        FROM search_chunks sc LEFT JOIN article_versions av ON av.id=sc.article_version_id
        WHERE sc.norm_id IN (SELECT norm_id FROM crawl_items WHERE crawl_run_id=%s)
        AND (av.id IS NULL OR av.norm_version_id=(SELECT nv.id FROM norm_versions nv
          WHERE nv.norm_id=sc.norm_id ORDER BY (nv.version_type='actualizada') DESC,nv.id DESC LIMIT 1))
        ORDER BY sc.norm_id,sc.id''', (run_id,)).fetchall()


def progress(con, job_id, stage, done, total):
    con.execute('UPDATE crawl_jobs SET progress=progress || %s WHERE id=%s',
                (Jsonb({stage: {'done': done, 'total': total}}), job_id))
    con.commit()


def document(con, chunk_id):
    row = con.execute('''SELECT n.infoleg_id,a.article_label FROM search_chunks sc
        JOIN norms n ON n.id=sc.norm_id LEFT JOIN article_versions av ON av.id=sc.article_version_id
        LEFT JOIN articles a ON a.id=av.article_id WHERE sc.id=%s''', (chunk_id,)).fetchone()
    con.commit()
    return f'InfoLEG {row[0]}' + (f' · {row[1]}' if row[1] else ' · ficha')


def classify_batch(con, dsn, run_id, job_id, report=None):
    from laya import Router
    router = Router(device='cpu', max_loaded=1, revision=MODEL_REVISION)
    loaded = False

    def ensure_model():
        nonlocal loaded
        if loaded or not report:
            return
        prepare_model('convaiinnovations/laya', MODEL_REVISION, [
            'multilingual/' + name for name in ('rl_agent_config.json', 'encoder/config.json',
                'tokenizer/tokenizer_config.json', 'tokenizer/tokenizer.json', 'model.safetensors')
        ], 'Laya', report)
        router.load('multilingual')
        loaded = True

    rows = con.execute("""SELECT n.id,n.infoleg_id,n.canonical_title,n.subject,n.summary,nv.id,nv.version_type,nv.full_text_original
        FROM norms n JOIN LATERAL (SELECT * FROM norm_versions WHERE norm_id=n.id
            ORDER BY (version_type='actualizada') DESC,id DESC LIMIT 1) nv ON true
        WHERE n.id IN (SELECT norm_id FROM crawl_items WHERE crawl_run_id=%s) ORDER BY n.infoleg_id""", (run_id,)).fetchall()
    con.commit()
    progress(con,job_id,'classify',0,len(rows))
    for done,(norm_id,infoleg_id,title,subject,summary,version_id,version,text) in enumerate(rows,1):
        heading = '\n'.join(filter(None,[title,subject,summary]))
        key = 'topics:norm:' + hashlib.sha256(f'{run_id}|{norm_id}|{version_id}|{TOPICS_VERSION}|{MODEL_REVISION}|{heading}|{text}'.encode()).hexdigest()
        exists = con.execute('SELECT 1 FROM review_tasks WHERE fingerprint=%s', (key,)).fetchone()
        if not exists:
            ensure_model()
            if report:
                report('Clasificando la norma con contexto', document=f'InfoLEG {infoleg_id}')
            articles = [dict(zip(('label','text'),r)) for r in con.execute("""SELECT a.article_label,av.text_normalized FROM article_versions av JOIN articles a ON a.id=av.article_id WHERE av.norm_version_id=%s ORDER BY a.ordinal,av.id""", (version_id,))]
            if not articles and text:
                articles=[{'label':'Texto completo','text':text}]
            result=classify_norm_topics(router,heading,articles,on_fragment=(
                lambda label,number:report('Clasificando contenido con contexto',document=f'InfoLEG {infoleg_id} · artículo {label}',fragment=number)) if report else None)
            result.update(model_revision=MODEL_REVISION,norm_version_id=version_id,version=version,source_hash=hashlib.sha256((text or '').encode()).hexdigest())
            save_task(con,run_id,norm_id,'pasaje',text or heading,result,key=key)
        con.execute("""UPDATE review_tasks SET superseded=true,superseded_reason='Reemplazado por clasificación con contexto de la norma; historial conservado'
            WHERE crawl_run_id=%s AND norm_id=%s AND model_result->>'category'='temas' AND fingerprint<>%s AND NOT superseded""", (run_id,norm_id,key))
        con.commit()
        progress(con,job_id,'classify',done,len(rows))
    # Reutiliza el modelo cargado para contrastar relaciones parser/Laya.
    prepare_review(['--dsn', dsn, '--run-id', str(run_id)], router=router, report=report, ensure_model=ensure_model)


def embed_batch(con, run_id, job_id, report=None):
    loaded = False
    rows = chunks(con, run_id)
    con.commit()
    progress(con, job_id, 'embeddings', 0, len(rows))
    for done, (chunk_id, _, _, text, source_hash) in enumerate(rows, 1):
        # Cada documento se escribe en una transacción: nunca queda media indexación.
        exists = con.execute('SELECT 1 FROM semantic_passages WHERE chunk_id=%s AND source_hash=%s AND model_revision=%s LIMIT 1',
                             (chunk_id, source_hash, EMBEDDING_VERSION)).fetchone()
        con.commit()
        if not exists and text.strip():
            if report and not loaded:
                prepare_model(EMBEDDING_MODEL, EMBEDDING_REVISION, [
                    'config.json', 'tokenizer_config.json', 'special_tokens_map.json',
                    'tokenizer.json', 'sentencepiece.bpe.model', 'model.safetensors',
                ], 'E5', report)
                runtime()
                loaded = True
            label = document(con, chunk_id) if report else ''
            if report:
                report('Dividiendo texto para búsqueda semántica', document=label)
            parts = list(fragments(text))
            vectors = []
            for ordinal, part in enumerate(parts, 1):
                if report:
                    report('Generando embeddings con E5', document=label, fragment=ordinal, fragments_total=len(parts))
                vectors.append(encode([part])[0])
            with con.transaction():
                for ordinal, (part, vector) in enumerate(zip(parts, vectors)):
                    con.execute('''INSERT INTO semantic_passages(chunk_id,source_hash,ordinal,content,embedding,model_revision)
                        VALUES (%s,%s,%s,%s,%s::vector,%s) ON CONFLICT DO NOTHING''',
                        (chunk_id, source_hash, ordinal, part, str(vector), EMBEDDING_VERSION))
        progress(con, job_id, 'embeddings', done, len(rows))
        if report:
            report('Texto indexado' if not exists else 'Texto ya indexado; reutilizado', document=document(con, chunk_id))


def main():
    stage, run_id, job_id = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    dsn = os.environ['PIPELINE_DSN']
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    os.environ.setdefault('HF_HOME', str(Path(__file__).resolve().parent / '.laya-cache'))
    with psycopg.connect(dsn) as con, JobActivity(dsn, job_id) as report:
        if stage == 'classify':
            classify_batch(con, dsn, run_id, job_id, report=report)
        elif stage == 'embeddings':
            embed_batch(con, run_id, job_id, report=report)
        else:
            raise ValueError('Etapa desconocida')


if __name__ == '__main__':
    main()
