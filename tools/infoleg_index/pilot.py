"""Piloto aislado: solo lee fuentes jurídicas y escribe analysis_pilot_* / su trabajo."""
import gc
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'web' / 'review') if len(Path(__file__).resolve().parents) > 2 else '/app')
from pilot_contract import (VERSION, PROMPT_VERSION, DEFAULT_MODEL, SCHEMA, INSTRUCTIONS,
                            fingerprint, empty_result, validate_result, analysis_resources)
from parser import parse_articles, parse_card, PARSER_VERSION
from references import extract_references
from legal_parser import extract_legal_changes, normalize_norm_number, NOTE_BLOCK
from text_integrity import check_document


class PilotError(Exception):
    def __init__(self, message, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}


def request_json(path, key, payload=None):
    request = Request('https://openrouter.ai/api/v1/' + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=120) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise PilotError('La respuesta supera el límite del piloto; no se utilizó parcialmente.')
            return json.loads(raw), dict(response.headers)
    except HTTPError as exc:
        reasons = {401: 'La clave de OpenRouter no fue aceptada. Revisá OPENROUTER_API_KEY en el crawler.',
            402: 'OpenRouter no habilitó la solicitud gratuita para esta cuenta.',
            404: 'No hay un endpoint compatible para el modelo fijo.',
            429: 'Se agotó el cupo o límite temporal de OpenRouter. Retomá el piloto más tarde.'}
        raise PilotError(reasons.get(exc.code, f'OpenRouter devolvió HTTP {exc.code}. Los resultados guardados se conservan.')) from None
    except (URLError, TimeoutError, OSError):
        raise PilotError('No se pudo completar la conexión con OpenRouter. Podés retomar sin perder resultados.') from None
    except (ValueError, UnicodeError):
        raise PilotError('OpenRouter devolvió una respuesta que no es JSON válido.') from None


def preflight(model):
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not key:
        raise PilotError('Falta OPENROUTER_API_KEY en el contenedor crawler. Configurala y recreá ese servicio.')
    if not model.endswith(':free'):
        raise PilotError('El piloto solo admite un modelo fijo con etiqueta :free; no se habilitan modelos pagos.')
    data, _ = request_json('models/' + quote(model, safe='/:') + '/endpoints', key)
    eligible = []
    for endpoint in data.get('data', {}).get('endpoints', []):
        pricing = endpoint.get('pricing', {})
        try:
            free = all(float(pricing.get(k, '1')) == 0 for k in ('prompt', 'completion'))
        except (ValueError, TypeError):
            free = False
        if free and {'structured_outputs', 'response_format'} <= set(endpoint.get('supported_parameters', [])):
            eligible.append(endpoint)
    if not eligible:
        raise PilotError('El modelo fijo no tiene endpoints gratuitos con salida estructurada disponibles. No se eligió otro modelo.')
    return {'context_length': min(e.get('context_length') or 0 for e in eligible),
            'endpoints': [e.get('name', e.get('provider_name', '')) for e in eligible]}


def infer(snapshot, laya, model, capacity):
    # Bytes UTF-8 son un límite superior conservador de tokens del documento; no se recorta texto.
    content = json.dumps({'identity': snapshot['identity'], 'units': snapshot['units'],
        'candidates':snapshot['candidates'],'unverified_resources':analysis_resources(snapshot.get('resources_not_checked',[])),
        'laya':[{k:v for k,v in r.items() if k!='seconds'} for r in laya.get('relations',[])]},ensure_ascii=False)
    if capacity <= 0 or len((INSTRUCTIONS + content).encode()) + 12000 > capacity:
        raise PilotError('La entrada completa excede el presupuesto conservador del contexto; requiere un análisis por partes. No se truncó.')
    payload = {'model': model, 'messages': [{'role': 'system', 'content': INSTRUCTIONS},
        {'role': 'user', 'content': content}], 'temperature': 0, 'max_tokens': 8192,
        'provider': {'require_parameters': True, 'max_price': {'prompt': 0, 'completion': 0}},
        'response_format': {'type': 'json_schema', 'json_schema': {'name': 'jurisai_norm', 'strict': True, 'schema': SCHEMA}}}
    started = time.monotonic()
    try:
        raw, headers = request_json('chat/completions', os.environ['OPENROUTER_API_KEY'].strip(), payload)
    except PilotError as exc:
        exc.metadata = {'requested':True,'seconds':round(time.monotonic()-started,3)}
        raise
    if not isinstance(raw,dict):
        raise PilotError('OpenRouter devolvió una respuesta con estructura inesperada.',{'requested':True})
    def safe_text(value):
        return value.replace(os.environ['OPENROUTER_API_KEY'].strip(),'[secreto omitido]')[:200] if isinstance(value,str) else None
    def remaining(name):
        value = next((v for k,v in headers.items() if k.lower()==name),None)
        return value if isinstance(value,str) and value.isdecimal() else None
    raw_usage = raw.get('usage') if isinstance(raw.get('usage'),dict) else {}
    usage = {k:v for k,v in raw_usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens','cost')
             and isinstance(v,(int,float)) and not isinstance(v,bool) and 0<=v<=10**12 and math.isfinite(v)}
    choices = raw.get('choices') or []
    metadata = {'requested':True,'model':safe_text(raw.get('model')),'provider':safe_text(raw.get('provider')),'request_id':safe_text(raw.get('id')),
        'usage':usage,'seconds':round(time.monotonic()-started,3),
        'remaining_requests':remaining('x-ratelimit-remaining-requests'),'remaining_tokens':remaining('x-ratelimit-remaining-tokens')}
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict) or choices[0].get('finish_reason') != 'stop':
        raise PilotError('La respuesta quedó incompleta o fue rechazada; no se aceptó una ficha parcial.', metadata)
    if usage.get('cost',0)>0:
        raise PilotError('El servicio informó un coste distinto de cero. Se detuvo el piloto; revisá OpenRouter antes de retomar.',metadata)
    returned = raw.get('model', '')
    if not isinstance(returned,str) or returned.removesuffix(':free') != model.removesuffix(':free'):
        raise PilotError('El servicio informó un modelo diferente del solicitado; el resultado no se aceptó.', metadata)
    try:
        result = json.loads(choices[0]['message']['content'])
        validate_result(result, snapshot)
    except (KeyError, TypeError, ValueError):
        raise PilotError('La ficha no cumple el esquema, la cobertura o la evidencia literal. Revisá el intento y retomá con instrucciones corregidas.', metadata) from None
    return result, metadata


def snapshot_source(row, source):
    norm_id, infoleg_id, kind, number, title, summary, body, jurisdiction = row
    source_id, version_kind, url, retrieved, html = source
    from bs4 import BeautifulSoup
    resources=[]
    soup=BeautifulSoup(html,'html.parser')
    for node in (soup.body or soup).find_all(['img','object','embed','iframe','a']):
        target=node.get('src') or node.get('data') or node.get('href')
        if not target or str(target).startswith('#'):
            continue
        if node.name=='a' and not (str(target).lower().split('?')[0].endswith(('.pdf','.doc','.docx','.zip'))
                                  or 'anexo' in node.get_text(' ',strip=True).lower()):
            continue
        resources.append({'kind':node.name,'url':urljoin(url,target) if not str(target).startswith('data:') else 'Recurso incorporado en HTML'})
    text, articles = parse_articles(html)
    units = [{'id': 'document', 'kind': 'document', 'label': 'Texto completo', 'text': text}]
    units += [{'id': f'article:{i}', 'kind': 'article', 'label': str(a['label']), 'text': a['text']}
              for i, a in enumerate(articles)]
    candidates = []
    for unit in (units[1:] or units):
        for ref in extract_references(unit['text']):
            from references import NORM
            location = next((m.start() for m in NORM.finditer(unit['text'])
                if normalize_norm_number(m['number'])==ref['target_number']), 0)
            evidence = [{'unit_id': unit['id'], 'quote': unit['text'][max(0,location-1000):location+1500]}]
            candidates.append({'source_unit_id': unit['id'], 'direction': 'outgoing',
                **{k: ref[k] for k in ('target_type', 'target_number', 'target_article', 'scope')},
                'type': ref['relation_type'], 'detail': ref['scope_detail'], 'evidence': evidence})
        for change in extract_legal_changes(unit['text']):
            literal = next((m.group(0) for m in NOTE_BLOCK.finditer(unit['text']) if ' '.join(m.group(0).split())==change.evidence_text),change.evidence_text)
            evidence = [{'unit_id':unit['id'],'quote':literal[:5000]}]
            candidates.append({'source_unit_id': unit['id'], 'direction': 'incoming',
                'target_type': change.amending_norm_type, 'target_number': change.amending_norm_number,
                'target_article': change.amending_article_reference, 'type': change.change_type,
                'scope': change.affected_scope if change.affected_scope in ('articulo','inciso','parrafo','anexo','expresion') else 'desconocido',
                'detail': unit['label'], 'evidence': evidence})
    result = {'identity': {'norm_id': norm_id, 'infoleg_id': infoleg_id, 'type': kind, 'number': number,
        'title': title or '', 'summary': summary or '', 'issuing_body': body or '', 'jurisdiction': jurisdiction},
        'source': {'id': source_id, 'version': 'actualizada' if version_kind == 'texto_actualizado' else 'original',
                   'url': url, 'retrieved_at': retrieved.isoformat(), 'html': html, 'hash': fingerprint(html)},
        'units':units,'candidates':candidates,'parser_version':PARSER_VERSION,'resources_not_checked':analysis_resources(resources),
        'integrity': check_document(html, text, [{'label': a['label'], 'text': a['text']} for a in articles])}
    return result


def reference_draft(snapshot):
    from topics import concepts_for, SUBJECT_HINTS, formal_article
    result = empty_result()
    units = snapshot['units'][1:] or snapshot['units']
    result['summary'] = {'text': snapshot['identity']['summary'],
                         'evidence': [{'unit_id': units[0]['id'], 'quote': units[0]['text'][:1000]}]}
    for unit in units:
        evidence = [{'unit_id': unit['id'], 'quote': unit['text'][:4000]}]
        for label in concepts_for(unit['text']):
            if label not in [c['label'] for c in result['concepts']]:
                result['concepts'].append({'label': label, 'role': 'central', 'evidence': evidence})
            matter = SUBJECT_HINTS.get(label)
            if matter and matter not in [m['label'] for m in result['matters']]:
                result['matters'].append({'label': matter, 'primary': not result['matters'], 'evidence': evidence})
        result['articles'].append({'unit_id': unit['id'], 'function': 'Comunicación formal' if formal_article(unit['text']) else ''})
    result['relations'] = snapshot['candidates']
    if (snapshot['identity']['type']=='Ley' and normalize_norm_number(snapshot['identity']['number'] or '')=='23600'
            and len(units)==4 and 'utilidad pública' in units[0]['text'] and '21.859' in units[2]['text']):
        result['summary'] = {'text': 'Declara de utilidad pública y sujeto a expropiación un inmueble destinado al Poder Judicial de la Nación, faculta a la Corte Suprema a determinar sus dependencias y establece la imputación de los gastos de adquisición.',
            'evidence':[{'unit_id':u['id'],'quote':u['text']} for u in units[:3]]}
        functions = ['Declara la utilidad pública y la expropiación del inmueble.',
            'Faculta a la Corte Suprema a determinar las dependencias judiciales.',
            'Establece la imputación de los gastos de adquisición.', 'Comunicación formal.']
        result['articles'] = [{'unit_id':u['id'],'function':function} for u,function in zip(units,functions)]
    result['doubts'] = [{'field': 'reference', 'reason': 'Borrador de reglas y sumario oficial. Revisar cada bloque; no es una respuesta jurídica validada.'}]
    return result


def prepare(con, pilot_id, report):
    pilot = con.execute('SELECT crawl_run_id,config FROM analysis_pilots WHERE id=%s', (pilot_id,)).fetchone()
    rows = con.execute('''SELECT DISTINCT n.id,n.infoleg_id,n.norm_type,n.number,n.canonical_title,n.summary,n.issuing_body,n.jurisdiction
        FROM norms n JOIN crawl_items ci ON ci.norm_id=n.id WHERE ci.crawl_run_id=%s ORDER BY n.id''', (pilot[0],)).fetchall()
    available = []
    excluded = []
    for row in rows:
        source = con.execute('''SELECT id,document_kind,url,retrieved_at,raw_content FROM norm_sources
            WHERE norm_id=%s AND crawl_run_id=%s AND document_kind IN ('texto_original','texto_actualizado')
            AND raw_content IS NOT NULL AND http_status=200
            ORDER BY (document_kind='texto_actualizado') DESC,id DESC LIMIT 1''', (row[0], pilot[0])).fetchone()
        if not source or not source[4].strip() or source[4].lstrip().startswith('%PDF'):
            excluded.append({'infoleg_id': row[1], 'reason': 'Sin texto HTML comprobable guardado en este lote'})
            continue
        snapshot = snapshot_source(row, source)
        card_source = con.execute("SELECT id,url,raw_content,retrieved_at FROM norm_sources WHERE norm_id=%s AND crawl_run_id=%s AND document_kind='ficha' AND raw_content IS NOT NULL ORDER BY id DESC LIMIT 1", (row[0],pilot[0])).fetchone()
        if card_source:
            card = parse_card(card_source[2],card_source[1])
            snapshot['identity'].update(type=card.norm_type,number=card.number,title=card.canonical_title or card.subject or '',
                summary=card.summary or '',issuing_body=card.issuing_body or '',
                dates={'sanction':str(card.sanction_date) if card.sanction_date else None,
                       'publication':str(card.publication_date) if card.publication_date else None})
            snapshot['card_source'] = {'id':card_source[0],'url':card_source[1], 'retrieved_at':card_source[3].isoformat(),'hash':fingerprint(card_source[2])}
        snapshot['source']['norm_version_id'] = con.execute('SELECT id FROM norm_versions WHERE source_id=%s ORDER BY id DESC LIMIT 1', (source[0],)).fetchone()
        snapshot['source']['norm_version_id'] = snapshot['source']['norm_version_id'][0] if snapshot['source']['norm_version_id'] else None
        snapshot['resources_not_checked'] += [{'id':r[0],'kind':r[1],'url':r[2]} for r in con.execute(
            "SELECT id,document_kind,url FROM norm_sources WHERE norm_id=%s AND crawl_run_id=%s AND document_kind IN ('pdf','html')", (row[0],pilot[0]))]
        if snapshot['units'][0]['text'].strip():
            available.append(snapshot)
        else:
            excluded.append({'infoleg_id': row[1], 'reason': 'La fuente guardada no tiene texto extraíble'})
    # Muestreo determinista: alterna extremos de longitud y referencias para no tomar solo los primeros IDs.
    buckets = {}
    for snapshot in available:
        key = (bool(snapshot['candidates']), snapshot['source']['version'])
        buckets.setdefault(key, []).append(snapshot)
    for values in buckets.values():
        values.sort(key=lambda s: (len(s['units'][0]['text']), s['identity']['infoleg_id']))
    selected = []
    for values in buckets.values():
        example = next((s for s in values if s['identity']['type']=='Ley' and normalize_norm_number(s['identity']['number'] or '')=='23600'),None)
        if example:
            selected.append(example)
            values.remove(example)
            break
    while len(selected) < 30 and any(buckets.values()):
        for values in buckets.values():
            if values and len(selected) < 30:
                selected.append(values.pop(-1 if len(selected) % 2 else 0))
    if not selected:
        raise PilotError('El lote no contiene textos HTML disponibles para preparar una muestra.')
    coverage = {'eligible': len(available), 'selected': len(selected), 'excluded': excluded,
        'with_references': sum(bool(s['candidates']) for s in selected),
        'updated': sum(s['source']['version'] == 'actualizada' for s in selected),
        'limitations': ['La selección usa fuentes disponibles del lote; no garantiza representación de todas las materias ni de anexos no descargados.']}
    for i, snapshot in enumerate(selected):
        key = fingerprint({'identity': snapshot['identity'], 'units': snapshot['units'],
            'parser':snapshot['parser_version'],'candidates':snapshot['candidates'],'contract':VERSION,
            'unverified_resources':[{k:r[k] for k in ('kind','url')} for r in snapshot.get('resources_not_checked',[])]})
        con.execute('''INSERT INTO analysis_pilot_items(pilot_id,norm_id,snapshot,reference,split,fingerprint)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(pilot_id,norm_id) DO NOTHING''',
            (pilot_id, snapshot['identity']['norm_id'], Jsonb(snapshot), Jsonb(reference_draft(snapshot)),
             'holdout' if i % 3 == 2 else 'development', key))
        if snapshot['identity']['type']=='Ley' and normalize_norm_number(snapshot['identity']['number'] or '')=='23600':
            con.execute('''UPDATE analysis_pilot_items SET metadata=metadata || %s WHERE pilot_id=%s AND norm_id=%s''',
                (Jsonb({'search_queries':['Expropiación de un inmueble para dependencias judiciales',
                    'Cómo se financia la adquisición del inmueble destinado al Poder Judicial']}),pilot_id,snapshot['identity']['norm_id']))
        report('Congelando fuente de la muestra', document=f"InfoLEG {snapshot['identity']['infoleg_id']}")
    reference_required = pilot[1].get('reference_required', True)
    con.execute('UPDATE analysis_pilots SET status=%s,phase=%s,coverage=%s WHERE id=%s',
                ('reference' if reference_required else 'running', 'prepare' if reference_required else 'analyze', Jsonb(coverage), pilot_id))
    con.commit()


def laya_stage(con, pilot_id, report):
    from topics import classify_norm_topics, VERSION as TOPICS_VERSION
    from try_laya import classify, MODEL_REVISION
    rows = con.execute('SELECT id,snapshot,laya FROM analysis_pilot_items WHERE pilot_id=%s ORDER BY id', (pilot_id,)).fetchall()
    router = None
    for pk, snapshot, cached in rows:
        if cached.get('revision') == MODEL_REVISION and cached.get('topics_version') == TOPICS_VERSION:
            continue
        report('Evaluando Laya por separado', document=f"InfoLEG {snapshot['identity']['infoleg_id']}")
        try:
            if router is None:
                from laya import Router
                from job_activity import prepare_model
                prepare_model('convaiinnovations/laya',MODEL_REVISION,
                    ['multilingual/'+name for name in ('rl_agent_config.json','encoder/config.json','tokenizer/tokenizer_config.json',
                        'tokenizer/tokenizer.json','model.safetensors')],'Laya',report)
                router = Router(device='cpu', max_loaded=1, revision=MODEL_REVISION)
                router.load('multilingual')
            units = snapshot['units'][1:] or snapshot['units']
            relations = []
            for candidate in snapshot['candidates']:
                unit = next(u for u in units if u['id'] == candidate['source_unit_id'])
                result = classify(router, {'text': unit['text']})
                relations.append({'candidate': candidate, 'prediction': result['prediction'], 'seconds':result['seconds'],
                    'score': result['predicted_probability'], 'complete': result['raw_result'].get('usage', {}).get('truncated') is False,
                    'reasons': result['review_reasons']})
            topics = classify_norm_topics(router, snapshot['identity']['title'],
                [{'label': u['label'], 'text': u['text']} for u in units],
                on_fragment=lambda label, fragment: report('Probando materias con Laya; resultado experimental',
                    document=f"InfoLEG {snapshot['identity']['infoleg_id']} · {label}", fragment=fragment))
            result = {'revision': MODEL_REVISION, 'topics_version': TOPICS_VERSION, 'relations': relations, 'topics': topics}
        except Exception:
            result = {'error': 'Laya no pudo completar este documento. Se conserva el fallo; la LLM puede analizar la fuente completa.'}
        con.execute('UPDATE analysis_pilot_items SET laya=%s WHERE id=%s', (Jsonb(result), pk))
        con.commit()
    del router
    gc.collect()


def analyze(con, pilot_id, job_id, report):
    model, config = con.execute('SELECT model,config FROM analysis_pilots WHERE id=%s', (pilot_id,)).fetchone()
    capacity = config['endpoint']['context_length']
    rows = con.execute('SELECT id,snapshot,laya,status,fingerprint FROM analysis_pilot_items WHERE pilot_id=%s ORDER BY id', (pilot_id,)).fetchall()
    for done, (pk, snapshot, laya, status, key) in enumerate(rows, 1):
        if status == 'done':
            continue
        cache_key = fingerprint([key,model,VERSION,PROMPT_VERSION,laya.get('revision'),analysis_resources(snapshot.get('resources_not_checked',[])),
            [{k:v for k,v in r.items() if k!='seconds'} for r in laya.get('relations',[])]])
        previous = con.execute('''SELECT result,metadata FROM analysis_pilot_items WHERE status='done'
            AND metadata->>'cache_key'=%s ORDER BY id LIMIT 1''', (cache_key,)).fetchone()
        report('Analizando ficha con OpenRouter', document=f"InfoLEG {snapshot['identity']['infoleg_id']}")
        try:
            if previous:
                result, metadata = previous
                metadata = {**{k:v for k,v in metadata.items() if not k.startswith('reference_') and k not in ('search_queries','attempts')}, 'reused': True}
            else:
                result, metadata = infer(snapshot, laya, model, capacity)
                metadata.update(cache_key=cache_key, contract=VERSION, prompt=PROMPT_VERSION, reused=False)
            con.execute("""UPDATE analysis_pilot_items SET status='done',result=%s,
                metadata=metadata || %s || jsonb_build_object('attempts',coalesce(metadata->'attempts','[]') || %s),error='' WHERE id=%s""",
                (Jsonb(result),Jsonb(metadata),Jsonb([] if previous else [metadata]),pk))
            con.commit()
        except PilotError as exc:
            con.execute("""UPDATE analysis_pilot_items SET status='error',error=%s,
                metadata=metadata || %s || jsonb_build_object('attempts',coalesce(metadata->'attempts','[]') || %s) WHERE id=%s""",
                (str(exc),Jsonb(exc.metadata),Jsonb([exc.metadata] if exc.metadata.get('requested') else []),pk))
            con.commit()
            raise
        con.execute("UPDATE crawl_jobs SET progress=progress || %s WHERE id=%s",
                    (Jsonb({'pilot': {'done': done, 'total': len(rows)}}), job_id))
        con.commit()
    con.execute("UPDATE analysis_pilots SET status='complete' WHERE id=%s", (pilot_id,))
    con.commit()


def main():
    from worker import database_dsn
    from job_activity import JobActivity
    stage, pilot_id, job_id = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    dsn = os.environ.get('PIPELINE_DSN') or database_dsn()
    try:
        with psycopg.connect(dsn) as con, JobActivity(dsn, job_id) as report:
            if stage == 'prepare':
                prepare(con, pilot_id, report)
            elif stage == 'preflight':
                model, config = con.execute('SELECT model,config FROM analysis_pilots WHERE id=%s', (pilot_id,)).fetchone()
                if config.get('contract') != VERSION or config.get('prompt') != PROMPT_VERSION:
                    raise PilotError('Cambió la versión del análisis. Prepará un piloto nuevo para conservar una comparación reproducible.')
                endpoint = preflight(model)
                con.execute("UPDATE analysis_pilots SET config=config || %s,status='running' WHERE id=%s",
                    (Jsonb({'endpoint': endpoint, 'contract': VERSION, 'prompt': PROMPT_VERSION}), pilot_id))
                con.commit()
            elif stage == 'laya':
                laya_stage(con, pilot_id, report)
            elif stage == 'analyze':
                analyze(con, pilot_id, job_id, report)
            else:
                raise PilotError('Etapa de piloto desconocida.')
    except PilotError as exc:
        with psycopg.connect(dsn) as con:
            con.execute("UPDATE crawl_jobs SET error=%s WHERE id=%s", (str(exc), job_id))
            con.execute("UPDATE analysis_pilots SET status='waiting' WHERE id=%s", (pilot_id,))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
