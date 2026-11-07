"""Consulta local E5 + pgvector con evidencia y exclusión de versiones obsoletas."""
from functools import lru_cache
from threading import Lock
from django.db import connection
from .embedding import VERSION, encode


_encoding_lock = Lock()


@lru_cache(maxsize=128)
def query_vector(query):
    with _encoding_lock:
        return str(encode([query], query=True)[0])


def search(query):
    if len(query) > 1000:
        raise ValueError('Acortá la consulta a 1.000 caracteres para buscar por significado.')
    # No cargar el modelo si todavía no hay documentos indexados.
    with connection.cursor() as cursor:
        cursor.execute('SELECT EXISTS(SELECT 1 FROM semantic_passages WHERE model_revision=%s)', [VERSION])
        if not cursor.fetchone()[0]:
            return []
    vector = query_vector(query)
    with connection.cursor() as cursor:
        # ponytail: recorrido exacto para el piloto; índice HNSW cuando el volumen lo justifique.
        cursor.execute('''WITH ranked AS (
          SELECT sc.norm_id,p.content,p.embedding <=> %s::vector AS distance,
            row_number() OVER (PARTITION BY sc.norm_id ORDER BY p.embedding <=> %s::vector) AS rank
          FROM semantic_passages p JOIN search_chunks sc ON sc.id=p.chunk_id
          LEFT JOIN article_versions av ON av.id=sc.article_version_id
          WHERE p.source_hash=md5(sc.content) AND p.model_revision=%s
          AND (av.id IS NULL OR av.norm_version_id=(SELECT nv.id FROM norm_versions nv
            WHERE nv.norm_id=sc.norm_id ORDER BY (nv.version_type='actualizada') DESC,nv.id DESC LIMIT 1)))
          SELECT norm_id,content FROM ranked WHERE rank=1 ORDER BY distance,norm_id LIMIT 40''',
                       [vector, vector, VERSION])
        return cursor.fetchall()
