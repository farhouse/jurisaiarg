"""Incremental, conservative references across batches. Missing targets are not human tasks."""
import hashlib
import re

from psycopg.types.json import Jsonb
from legal_parser import NOTE_BLOCK, normalize_norm_type, normalize_norm_number

NORM = re.compile(r'\b(?P<type>Ley|Decreto(?:\s+(?:DNU|Reglamentario))?|Resoluci[oó]n|Disposici[oó]n)\s*(?:(?:N(?:ro|[°ºo.])?\.?|N[uú]mero)\s*)?(?P<number>\d+(?:\.\d{3})*(?:/\d{2,4})?)(?!\d)', re.I)
ACTION = re.compile(r'\b(sustit[uú]yese|reempl[aá]zase|der[oó]gase|incorp[oó]rase|agr[eé]gase|modif[ií]case)\b', re.I)
ACTIONS = {'sustituyese':'sustituye', 'reemplazase':'sustituye', 'derogase':'deroga', 'incorporase':'incorpora', 'agregase':'incorpora', 'modificase':'modifica'}
ARTICLE = re.compile(r'\bart[ií]culos?\s+(\d+(?:\s*(?:bis|ter))?)', re.I)


def extract_references(text):
    from unicodedata import normalize, combining
    # Editorial amendments already have explicit direction in legal_changes.
    excluded = [(m.start(), m.end()) for m in NOTE_BLOCK.finditer(text)]
    for match in NORM.finditer(text):
        if any(a <= match.start() < b for a,b in excluded):
            continue
        before = text[:match.start()]
        # A previous sentence's imperative must not turn an unrelated citation into an amendment.
        boundaries = list(re.finditer(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚ])', before))
        start = max(before.rfind('\n'), before.rfind(';'), boundaries[-1].end() if boundaries else 0)
        clause = text[start:match.start()]
        clause = re.sub(r'^\s*(?:art[ií]culo|art\.)\s*\d+\s*[º°o]?\s*[-—–.]?\s*','',clause,flags=re.I)
        actions=list(ACTION.finditer(clause))
        action = actions[-1] if actions else None
        if action and ':' in clause[action.end():]:
            action=None
        matches=list(ARTICLE.finditer(clause))
        article=matches[-1] if matches else None
        relation, scope, detail = 'cita', 'articulo' if article else 'norma', ''
        if action:
            verb = ''.join(c for c in normalize('NFD', action.group().lower()) if not combining(c))
            relation = ACTIONS[verb]
            partial = re.search(r'\b(inciso|p[aá]rrafo)\s+([\w]+\)?)', clause, re.I)
            if partial:
                scope = 'inciso' if partial.group(1).lower() == 'inciso' else 'parrafo'
                detail = partial.group(2)
            # Several target articles require explicit disambiguation, never a guessed single target.
            if re.search(r'\bart[ií]culos\b', clause, re.I):
                relation = 'ambiguo'
        yield {'target_type':normalize_norm_type(match['type']), 'target_number':normalize_norm_number(match['number']),
               'target_article':article.group(1).strip() if article else '', 'relation_type':relation,
               'scope':scope, 'scope_detail':detail, 'evidence_text':text.strip()}


def resolve_pending(con):
    """Retry only unresolved identities against the entire catalogue, including new batches."""
    index = {}
    for pk, kind, number in con.execute('SELECT id,norm_type,number FROM norms WHERE norm_type IS NOT NULL AND number IS NOT NULL'):
        index.setdefault((normalize_norm_type(kind), normalize_norm_number(number)), []).append(pk)
    for pk, kind, number, old_resolution, old_candidates in con.execute("SELECT id,target_type,target_number,resolution,candidates FROM norm_references WHERE resolution <> 'resuelta'").fetchall():
        candidates = index.get((kind,number), [])
        resolution = 'resuelta' if len(candidates)==1 else 'ambigua' if candidates else 'pendiente'
        con.execute('UPDATE norm_references SET target_norm_id=%s,resolution=%s,candidates=%s WHERE id=%s',
                    (candidates[0] if len(candidates)==1 else None,resolution,Jsonb(candidates),pk))
    # Old editorial references can now resolve without reparsing the old batch or changing its review.
    for pk, raw in con.execute('SELECT id,amending_reference_raw FROM legal_changes WHERE amending_norm_id IS NULL').fetchall():
        match = NORM.search(raw)
        if match:
            candidates = index.get((normalize_norm_type(match['type']), normalize_norm_number(match['number'])), [])
            if len(candidates)==1:
                con.execute('UPDATE legal_changes SET amending_norm_id=%s,updated_at=now() WHERE id=%s', (candidates[0],pk))


def sync_references(con, norm_ids=None):
    query = '''SELECT av.id,av.norm_version_id,nv.norm_id,av.text_normalized FROM article_versions av
        JOIN norm_versions nv ON nv.id=av.norm_version_id'''
    rows = con.execute(query + (' WHERE nv.norm_id=ANY(%s)' if norm_ids is not None else ''), (norm_ids,) if norm_ids is not None else ()).fetchall()
    for article_id, version_id, norm_id, text in rows:
        for ref in extract_references(text):
            fingerprint = hashlib.sha256(f'{version_id}|{article_id}|{ref}'.encode()).hexdigest()
            con.execute('''INSERT INTO norm_references(source_norm_id,source_version_id,source_article_version_id,
                target_type,target_number,target_article,relation_type,scope,scope_detail,evidence_text,fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fingerprint) DO NOTHING''',
                (norm_id,version_id,article_id,ref['target_type'],ref['target_number'],ref['target_article'],ref['relation_type'],ref['scope'],ref['scope_detail'],ref['evidence_text'],fingerprint))
    resolve_pending(con)
    con.commit()


def prepare_reference_issues(con, run_id, router=None, report=None, ensure_model=None):
    from prepare_review import save_task
    from try_laya import classify
    rows=con.execute('''SELECT r.id,r.source_norm_id,r.source_article_version_id,r.evidence_text,r.relation_type,
        r.scope,r.scope_detail,r.resolution,r.assessment FROM norm_references r
        WHERE r.source_norm_id IN (SELECT norm_id FROM crawl_items WHERE crawl_run_id=%s)
        AND r.source_version_id=(SELECT id FROM norm_versions WHERE norm_id=r.source_norm_id
            ORDER BY (version_type='actualizada') DESC,id DESC LIMIT 1)''', (run_id,)).fetchall()
    for pk,norm_id,article_id,text,kind,scope,detail,resolution,assessment in rows:
        reasons=[]
        if resolution=='ambigua':
            reasons.append('referencia_ambigua')
        if kind=='ambiguo':
            reasons.append('relacion_ambigua')
        if kind!='cita' and router and not assessment:
            if ensure_model:ensure_model()
            if report:report('Contrastando una relación concreta',document=f'Referencia {pk}')
            result=classify(router,{'text':text})
            assessment={'prediction':result['prediction'],'probability':result['predicted_probability'],
                        'complete':result['raw_result'].get('usage',{}).get('truncated') is False}
            con.execute('UPDATE norm_references SET assessment=%s WHERE id=%s',(Jsonb(assessment),pk))
        if assessment:
            if assessment['prediction']!=kind:reasons.append('desacuerdo_parser_laya')
            if assessment['probability']<.75:reasons.append('probabilidad_baja')
            if not assessment['complete']:reasons.append('texto_truncado_o_no_verificado')
        if kind!='cita' and scope in ('inciso','parrafo') and not detail:
            reasons.append('alcance_parcial_sin_identificar')
        if reasons:
            save_task(con,run_id,norm_id,'pasaje',text,
                {'decision':'revision','prediction':kind,'review_reasons':reasons,'category':'relacion','reference_id':pk},
                article_id=article_id,key=f'reference:{run_id}:{pk}',reference_id=pk)
    con.commit()
