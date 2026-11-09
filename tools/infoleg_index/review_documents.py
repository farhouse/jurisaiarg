"""Document-scoped review preparation and non-destructive legacy queue migration."""
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'web' / 'review') if len(Path(__file__).resolve().parents)>2 else '/app')
from text_integrity import POLICY, check_document


def reparse_saved(con, run_id, report=None):
    """Upgrade extraction from saved HTML, without network access or deleting prior versions."""
    from parser import parse_articles
    from crawl_infoleg_pg import add_version
    rows=con.execute('''SELECT DISTINCT ON (norm_id,document_kind) id,norm_id,document_kind,raw_content
        FROM norm_sources WHERE crawl_run_id=%s AND http_status BETWEEN 200 AND 299
        AND document_kind IN ('texto_original','texto_actualizado') AND raw_content IS NOT NULL
        ORDER BY norm_id,document_kind,id DESC''',(run_id,)).fetchall()
    for source_id,norm_id,kind,html in rows:
        if html.lstrip().startswith('%PDF'):
            continue
        if report:report('Reprocesando HTML guardado; sin descargar',document=f'Norma {norm_id}')
        text,articles=parse_articles(html)
        with con.cursor() as cur:
            add_version(cur,norm_id,source_id,'original' if kind=='texto_original' else 'actualizada',text,articles,kind=='texto_actualizado')
    con.commit()


def index_result(con, run_id, norm_id, cached=None, rebuild=False):
    cached = cached or {}
    identity = con.execute('SELECT norm_type,number,issuing_body,summary FROM norms WHERE id=%s', (norm_id,)).fetchone()
    versions = con.execute('''SELECT DISTINCT ON (version_type) id,version_type,full_text_original,source_id
        FROM norm_versions WHERE norm_id=%s ORDER BY version_type,id DESC''', (norm_id,)).fetchall()
    articles, documents = [], []
    old_documents = {d['version']:d for d in cached.get('documents', [])}
    if rebuild:
        # The source belongs to this batch. Do not compare an older batch against a newer download.
        from parser import parse_articles, text_hash
        versions=[]
        for source_id,kind,html in con.execute('''SELECT DISTINCT ON (document_kind) id,document_kind,raw_content
            FROM norm_sources WHERE norm_id=%s AND crawl_run_id=%s AND http_status BETWEEN 200 AND 299
            AND document_kind IN ('texto_original','texto_actualizado') ORDER BY document_kind,id DESC''',(norm_id,run_id)):
            if not html or html.lstrip().startswith('%PDF'):continue
            text,_=parse_articles(html)
            version='original' if kind=='texto_original' else 'actualizada'
            row=con.execute('SELECT id FROM norm_versions WHERE norm_id=%s AND version_type=%s AND text_hash=%s',(norm_id,version,text_hash(text))).fetchone()
            if row:versions.append((row[0],version,text,source_id))
    for version_id, version, text, source_id in versions:
        group = [a for a in cached.get('articles', []) if a['version']==version] if 'articles' in cached and not rebuild else [
            dict(zip(('label','text'), row),version=version) for row in con.execute('''SELECT a.article_label,av.text_normalized
                FROM article_versions av JOIN articles a ON a.id=av.article_id WHERE av.norm_version_id=%s ORDER BY a.ordinal,av.id''', (version_id,))]
        if version in old_documents and not rebuild:
            text = old_documents[version]['text']
        source = con.execute('''SELECT id,raw_content,http_status FROM norm_sources WHERE norm_id=%s AND crawl_run_id=%s
            AND document_kind=%s ORDER BY id DESC LIMIT 1''', (norm_id,run_id,'texto_original' if version=='original' else 'texto_actualizado')).fetchone()
        html = source[1] if source and source[2] and 200<=source[2]<300 else None
        proof = check_document(html,text,group,version)
        documents.append({'version':version,'text':text or '', 'norm_version_id':version_id,
                          'source_id':source[0] if source else None, 'integrity':proof})
        articles.extend(group)
    reasons = ['copia_diferente'] if any(d['integrity']['status']=='diferencias' for d in documents) else []
    history=list(cached.get('document_history',[]))
    if rebuild and cached.get('documents') and cached['documents']!=documents:
        history.append({'documents':cached['documents'],'articles':cached.get('articles',[])})
    return {**cached, 'document_history':history, 'copy_policy':POLICY, 'identity':cached.get('identity') or dict(zip(('type','number','issuer','summary'), identity)),
            'documents':documents, 'articles':articles, 'decision':'revision' if reasons else 'aceptacion_automatica',
            'review_reasons':reasons, 'requires_processing':not documents or any(d['integrity']['status']=='no_comprobable' for d in documents)}


def refresh_queue(con, run_id=None):
    """No votes/events/drafts are deleted or reinterpreted as full-document approvals."""
    query = "SELECT id,crawl_run_id,norm_id,model_result FROM review_tasks WHERE kind='indice'"
    for task_id, run, norm_id, cached in con.execute(query + (' AND crawl_run_id=%s' if run_id else ''), (run_id,) if run_id else ()).fetchall():
        result = cached if cached.get('copy_policy')==POLICY else index_result(con,run,norm_id,cached)
        con.execute('UPDATE review_tasks SET model_result=%s,decision=%s,reasons=%s WHERE id=%s',
                    (Jsonb(result),result['decision'],Jsonb(result['review_reasons']),task_id))
        old = con.execute("""SELECT id,model_result FROM review_tasks WHERE crawl_run_id=%s AND norm_id=%s
            AND kind='pasaje' AND model_result->>'category'='temas' AND model_result->>'catalog_version' IS DISTINCT FROM 'materias-contexto-v2'
            AND NOT superseded""", (run,norm_id)).fetchall()
        if old:
            # Old isolated predictions remain accessible as history, not hundreds of mandatory tasks.
            matters, concepts = {}, set()
            for _, model in old:
                for matter in model.get('matters', []):
                    if (matter.get('score') or 0) > (matters.get(matter['label'],{}).get('score',-1) or 0):
                        matters[matter['label']] = matter
                concepts.update(model.get('concepts', []))
            legacy = {'category':'temas','scope':'norma','catalog_version':'materias-contexto-v2','contextual':False,
                      'requires_processing':True,'matters':list(matters.values()),'concepts':sorted(concepts),
                      'previous_tasks':[row[0] for row in old], 'review_reasons':[]}
            con.execute('''INSERT INTO review_tasks(crawl_run_id,norm_id,kind,source_text,decision,reasons,model_result,fingerprint)
                SELECT %s,%s,'pasaje',%s,'aceptacion_automatica','[]',%s,%s
                WHERE NOT EXISTS (SELECT 1 FROM review_tasks WHERE norm_id=%s AND crawl_run_id=%s
                    AND NOT superseded AND model_result->>'contextual'='true')
                ON CONFLICT(fingerprint) DO NOTHING''',
                (run,norm_id,'\n\n'.join(d['text'] for d in result['documents']),Jsonb(legacy),f'topics:norm:{run}:{norm_id}:legacy-v2',norm_id,run))
            con.execute("UPDATE review_tasks SET superseded=true,superseded_reason='Consolidado en la ficha de la norma; historial conservado' WHERE id=ANY(%s)", ([row[0] for row in old],))
    # Ordinary provisions and negative model predictions are not relation issues.
    con.execute("""UPDATE review_tasks SET superseded=true,superseded_reason='Sin cambio jurídico concreto que revisar; historial conservado'
        WHERE kind='pasaje' AND model_result->>'category' IS DISTINCT FROM 'temas'
        AND NOT superseded AND prediction='ninguno' AND legal_change_id IS NULL AND reference_id IS NULL
        AND source_text !~* '(sustit[uú]yese|der[oó]gase|incorp[oó]rase|modif[ií]case|sustituido por|derogado por|incorporado por)'"""
        + (' AND crawl_run_id=%s' if run_id else ''), (run_id,) if run_id else ())
    for draft_id, data in con.execute('SELECT id,data FROM review_drafts').fetchall():
        kept, archived = {}, dict(data.get('archived_items', {}))
        for item_key, item in data.get('items', {}).items():
            task = con.execute('SELECT kind,superseded,model_result FROM review_tasks WHERE id=%s',(item['task_id'],)).fetchone()
            old_check = (item.get('fields', {}).get('check') or [''])[0]
            document_keys={d['integrity'].get('key') for d in task[2].get('documents',[])} if task else set()
            if task and (task[1] or task[0]=='indice' and task[2].get('copy_policy')==POLICY and old_check not in document_keys):
                archived[item_key]=item
            else:
                kept[item_key]=item
        if len(kept)!=len(data.get('items', {})):
            con.execute('UPDATE review_drafts SET data=%s,revision=revision+1 WHERE id=%s',
                        (Jsonb({**data,'items':kept,'archived_items':archived,'cursor':{}}),draft_id))
    con.commit()
