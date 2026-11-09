"""Controles de copia y visor de la fuente, independientes de la clasificación."""
import hashlib
import json
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from django.http import Http404, HttpResponse
from django.db.models import Q
from django.urls import reverse

from .models import ArticleVersion, NormSource, NormVersion, NormReview
from .text_integrity import POLICY


ISSUES = [('identity', 'Identidad incorrecta'), ('missing_text', 'Falta texto'),
          ('changed_text', 'Texto diferente'), ('numbering', 'Numeración incorrecta'),
          ('annexes', 'Anexos incompletos')]
STATUS_LABELS = {'validada': 'Correcto', 'rechazada': 'Hay diferencias', 'pendiente': 'Pendiente'}
VERSIONS = {'original': 'Texto original', 'actualizada': 'Texto actualizado'}


def articles_for(task):
    if 'articles' in task.model_result:
        return task.model_result['articles']
    return [{'label': av.article.article_label, 'version': av.norm_version.version_type,
             'text': av.text_normalized} for av in ArticleVersion.objects.filter(
                 article__norm=task.norm).select_related('article', 'norm_version')
            .order_by('norm_version_id', 'article__ordinal')]


def safe_official_url(value):
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme not in ('http', 'https') or url.hostname not in (
            'servicios.infoleg.gob.ar', 'infoleg.gob.ar', 'www.infoleg.gob.ar') or url.username or url.password:
        return None
    return value.replace('http://', 'https://', 1)


def copy_context(task, vote, request, current_url):
    if task.model_result.get('copy_policy') == POLICY:
        return document_context(task,vote,request,current_url)
    articles = articles_for(task)
    sources = {}
    for source in NormSource.objects.filter(norm_id=task.norm_id, crawl_run_id=task.crawl_run_id,
            http_status__gte=200, http_status__lt=300).exclude(raw_content__isnull=True).exclude(raw_content='').exclude(raw_content__regex=r'^\s*%PDF')\
            .defer('raw_content').order_by('id'):
        if source.document_kind in ('ficha', 'texto_original', 'texto_actualizado'):
            sources[source.document_kind] = source
    versions = [v for v in VERSIONS if any(a['version'] == v for a in articles)
                or ('texto_original' if v == 'original' else 'texto_actualizado') in sources]
    versions = versions or ['original']
    selected_version = request.GET.get('version', versions[0])
    if selected_version not in versions:
        raise Http404
    saved = vote.checks if vote else {}
    checks = []

    def add(key, label, step, source, **extra):
        result = saved.get(key, {})
        check = {'key': key, 'label': label, 'step': step, 'source': source,
                 'status': result.get('status', 'pendiente'), 'saved': bool(result),
                 'notes': result.get('notes', ''), 'issues': result.get('issues', []), **extra}
        check['status_label'] = STATUS_LABELS[check['status']] if result else 'Sin revisar'
        check['complete'] = check['status'] != 'pendiente'
        params = {'step': step, 'version': check.get('version', selected_version)}
        if 'index' in check:
            params['article'] = check['index']
        check['url'] = current_url + '&' + urlencode(params)
        checks.append(check)
        return check

    identity = task.model_result.get('identity') or {'type': task.norm.norm_type,
        'number': task.norm.number, 'issuer': task.norm.issuing_body, 'summary': task.norm.summary}
    add('identity', 'Identidad de la norma', 'identity', sources.get('ficha'), identity=identity)
    for version in versions:
        version_articles = [a for a in articles if a['version'] == version]
        source = sources.get('texto_original' if version == 'original' else 'texto_actualizado')
        if not version_articles:
            add('articles-missing:' + version, 'Articulado sin extraer · ' + VERSIONS[version], 'articles',
                source or sources.get('ficha'), version=version, version_label=VERSIONS[version], missing=True, index=0)
        for index, article in enumerate(version_articles):
            digest = hashlib.sha256(json.dumps(article, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
            add('article:' + digest, f"Artículo {article['label']} · {VERSIONS[version]}", 'articles', source,
                version=version, version_label=VERSIONS[version], article=article, index=index,
                position=index + 1, article_total=len(version_articles))
    for version in versions:
        source = sources.get('texto_original' if version == 'original' else 'texto_actualizado')
        add('annexes:' + version, 'Anexos · ' + VERSIONS[version], 'annexes', source or sources.get('ficha'),
            version=version, version_label=VERSIONS[version])
    step = request.GET.get('step', 'identity')
    if step not in ('identity', 'articles', 'annexes', 'summary'):
        raise Http404
    candidates = [c for c in checks if c['step'] == step and c.get('version', selected_version) == selected_version]
    if step == 'articles' and 'article' in request.GET:
        try:
            index = int(request.GET['article'])
        except ValueError:
            raise Http404
        current = next((c for c in candidates if c['index'] == index), None)
        if not current:
            raise Http404
    else:
        current = next((c for c in candidates if not c['complete']), candidates[0] if candidates else None)
    steps = []
    for key, label in [('identity', 'Identidad'), ('articles', 'Artículos'), ('annexes', 'Anexos'), ('summary', 'Resumen')]:
        group = [c for c in checks if c['step'] == key]
        done = sum(c['complete'] for c in group)
        steps.append({'key': key, 'label': label, 'done': done, 'total': len(group),
                      'complete': bool(group) and done == len(group),
                      'url': current_url + '&' + urlencode({'step': key, 'version': selected_version})})
    summary_url = steps[-1]['url']
    source = current['source'] if current else None
    source_url = reverse('review:copy_source', args=[task.pk, source.pk]) if source else None
    if source_url and step in ('articles', 'annexes') and not current.get('missing'):
        focus = current['article']['label'] if step == 'articles' else 'annexes'
        source_url += '?' + urlencode({'focus': focus}) + '#jurisai-focus'
    position = checks.index(current) if current else -1
    context = {'steps': steps, 'step': step, 'checks': checks, 'control': current,
               'versions': [{'key': v, 'label': VERSIONS[v]} for v in versions],
               'version': selected_version, 'version_label': VERSIONS[selected_version],
               'source': source, 'source_url': source_url,
               'source_label': 'Ficha oficial' if source and source.document_kind == 'ficha' else VERSIONS[selected_version],
               'official_url': safe_official_url(source.url) if source else None,
               'check_count': len(checks), 'checked_count': sum(c['complete'] for c in checks),
               'previous_url': checks[position - 1]['url'] if position > 0 else None,
               'next_url': checks[position + 1]['url'] if 0 <= position < len(checks) - 1 else summary_url,
               'summary_url': summary_url, 'legacy_vote': bool(vote and not saved),
               'snapshot': 'articles' in task.model_result,
               'version_articles': [a for a in articles if a['version'] == selected_version]}
    if step == 'annexes':
        documents = task.model_result.get('documents', [])
        doc = next((d for d in documents if d['version'] == selected_version), None)
        if doc:
            full_text = doc['text'] or ''
        elif source:
            full_text = NormVersion.objects.filter(norm=task.norm, source_id=source.pk).values_list('full_text_original', flat=True).first() or ''
        else:
            full_text = ''
        # Se muestra el texto completo si no hay un encabezado inequívoco de anexo.
        match = re.search(r'(?im)^\s*ANEXO(?:\s+[IVXLC\d.-]+)?\s*$', full_text)
        context.update(annex_text=full_text[match.start():] if match else full_text,
                       annex_snapshot=bool(doc))
    return context


def document_context(task, vote, request, current_url):
    documents = task.model_result.get('documents', [])
    versions = [d['version'] for d in documents] or ['original']
    selected = request.GET.get('version') or ('actualizada' if 'actualizada' in versions else versions[0])
    if selected not in versions:
        raise Http404
    checks=[]
    for doc in documents:
        proof=doc['integrity']
        source=NormSource.objects.filter(pk=doc.get('source_id'),norm_id=task.norm_id).first()
        key=proof.get('key','document:'+doc['version']+':missing:'+hashlib.sha256(doc.get('text','').encode()).hexdigest())
        saved=vote.checks.get(key) if vote else None
        if not saved and getattr(request,'user',None):
            # Same norm, same version, same source/local content. Batch arrival is irrelevant.
            for previous in NormReview.objects.filter(task__norm_id=task.norm_id,user_id=request.user.pk).order_by('-updated_at'):
                candidate=previous.checks.get(key)
                if candidate and candidate.get('scope')=='document':
                    saved=candidate;break
        saved=saved or {}
        checks.append({'key':key,'label':'Texto completo · '+VERSIONS.get(doc['version'],doc['version']),
            'step':'document','version':doc['version'],'version_label':VERSIONS.get(doc['version'],doc['version']),
            'text':doc['text'],'source':source,'proof':proof,'scope':'document',
            'status':saved.get('status','pendiente'),'notes':saved.get('notes',''),'issues':saved.get('issues',[]),
            'saved':bool(saved),'complete':saved.get('status') in ('validada','rechazada'),
            'status_label':STATUS_LABELS.get(saved.get('status'),'Sin revisión humana'),
            'url':current_url+'&'+urlencode({'version':doc['version'],'step':'document'})})
    current=next((c for c in checks if c['version']==selected),None)
    source=current['source'] if current else None
    return {'document_mode':True,'step':'document','steps':[],'checks':checks,'control':current,
        'version':selected,'version_label':VERSIONS.get(selected,selected),
        'versions':[{'key':v,'label':VERSIONS.get(v,v)} for v in versions],
        'source':source,'source_url':reverse('review:copy_source',args=[task.pk,source.pk]) if source else None,
        'official_url':safe_official_url(source.url) if source else None,
        'source_label':'Fuente de la versión revisada','check_count':len(checks),
        'checked_count':sum(c['complete'] for c in checks),'summary_url':current_url+'&finish=1',
        'next_url':current_url+'&finish=1','snapshot':True,'legacy_vote':bool(vote and not any(k.startswith('document:') for k in vote.checks))}


def aggregate(checks, saved):
    statuses = [saved.get(c['key'], {}).get('status', 'validada' if c.get('proof',{}).get('status')=='coincide' else 'pendiente') for c in checks]
    if 'rechazada' in statuses:
        return 'rechazada'
    return 'validada' if statuses and all(s == 'validada' for s in statuses) else 'pendiente'


class FocusAnchor(HTMLParser):
    """Agrega un ancla de navegación sin usar el parser jurídico para reconstruir la fuente."""
    def __init__(self, focus):
        super().__init__(convert_charrefs=False)
        self.parts, self.anchored, self.inert = [], False, 0
        self.pattern = (re.compile(r'^\s*ANEXO(?:\s+[IVXLC\d.-]+)?\s*$', re.I) if focus == 'annexes' else
                        re.compile(r'(?:ART[IÍ]CULO|ART\.)\s*' + re.escape(focus) + r'(?!\d)', re.I)) if focus else None

    def handle_starttag(self, tag, attrs):
        self.parts.append(self.get_starttag_text())
        if tag in ('script', 'style'):
            self.inert += 1

    def handle_startendtag(self, tag, attrs):
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.parts.append(f'</{tag}>')
        if tag in ('script', 'style'):
            self.inert = max(0, self.inert - 1)

    def handle_data(self, data):
        match = self.pattern.search(data) if self.pattern and not self.anchored and not self.inert else None
        if match:
            self.parts.append(data[:match.start()] + '<span id="jurisai-focus">' + data[match.start():match.end()] + '</span>' + data[match.end():])
            self.anchored = True
        else:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_comment(self, data):
        self.parts.append('<!--' + data + '-->')

    def handle_decl(self, decl):
        self.parts.append('<!' + decl + '>')


def source_response(task, source_id, focus):
    from django.shortcuts import get_object_or_404
    source = get_object_or_404(available_sources(task), pk=source_id)
    if not source.raw_content or source.raw_content.lstrip().startswith('%PDF') or source.document_kind not in ('ficha', 'texto_original', 'texto_actualizado'):
        raise Http404
    viewer = FocusAnchor(focus[:100])
    viewer.feed(source.raw_content)
    # El documento externo queda aislado aun si contiene scripts, formularios o HTML malicioso.
    official = safe_official_url(source.url)
    base = f'<base href="{escape(official, quote=True)}" target="_blank">' if official else ''
    document = ''.join(viewer.parts)
    head = re.search(r'<head\b[^>]*>', document, re.I)
    if head:
        document = document[:head.end()] + base + document[head.end():]
    else:
        doctype = re.match(r'\s*<!doctype[^>]*>', document, re.I)
        position = doctype.end() if doctype else 0
        document = document[:position] + base + document[position:]
    response = HttpResponse(document, content_type='text/html; charset=utf-8')
    origins = 'https://servicios.infoleg.gob.ar https://infoleg.gob.ar https://www.infoleg.gob.ar'
    response['Content-Security-Policy'] = (f"default-src 'none'; style-src 'unsafe-inline' {origins}; "
        f"img-src data: {origins}; base-uri {origins}; form-action 'none'; frame-ancestors 'self'; sandbox")
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def available_sources(task):
    scope = Q(norm_id=task.norm_id, crawl_run_id=task.crawl_run_id)
    if task.kind == 'indice' and task.model_result.get('copy_policy')==POLICY:
        scope |= Q(norm_id=task.norm_id,pk__in=[d.get('source_id') for d in task.model_result.get('documents',[])])
    if task.kind == 'pasaje' and task.reference_id:
        scope |= Q(norm_id=task.reference.target_norm_id) if task.reference.target_norm_id else Q(pk__in=[])
    if task.kind == 'pasaje' and task.legal_change_id:
        related = {task.legal_change.amending_norm_id, task.legal_change.affected_norm_id} - {task.norm_id, None}
        scope |= Q(norm_id__in=related)
    return NormSource.objects.filter(scope, http_status__gte=200, http_status__lt=300,
        document_kind__in=['ficha', 'texto_original', 'texto_actualizado']).exclude(
        raw_content__isnull=True).exclude(raw_content='').exclude(raw_content__regex=r'^\s*%PDF')
