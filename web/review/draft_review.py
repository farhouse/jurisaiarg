"""Private resumable progress; final decisions use the existing audited workflow."""
from types import SimpleNamespace
from urllib.parse import urlencode

from django import forms
from django.db import transaction
from django.http import Http404, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import copy_review, passage_review
from .models import NormReview, ReviewDraft, ReviewTask


def key(task, control=None):
    return str(task.pk) + (':' + control['key'] if control else '')


def fields_data(values):
    data = QueryDict('', mutable=True)
    for name, values in values.items():
        data.setlist(name, values)
    return data


def overlay(form, item):
    if item:
        for name, field in form.fields.items():
            values = item['fields'].get(name, [])
            form.initial[name] = values if isinstance(field, forms.MultipleChoiceField) else (values[0] if values else '')


def preview(copy, task, draft):
    if not copy or not draft:
        return
    for check in copy['checks']:
        item = draft.data.get('items', {}).get(key(task, check))
        if item:
            data = fields_data(item['fields'])
            check.update(status=data.get('status') or 'pendiente', notes=data.get('notes', ''),
                         issues=data.getlist('issues'), saved=True)
            check['complete'] = check['status'] != 'pendiente'
            check['status_label'] = copy_review.STATUS_LABELS[check['status']]
    copy['checked_count'] = sum(c['complete'] for c in copy['checks'])
    for step in copy['steps']:
        checks = [c for c in copy['checks'] if c['step'] == step['key']]
        step['done'] = sum(c['complete'] for c in checks)
        step['complete'] = bool(checks) and step['done'] == len(checks)


def save(request, root_id, task, form, control, base_revision):
    """Accept incomplete business decisions, validate field boundaries, never publish votes."""
    try:
        revision = int(request.POST.get('draft_revision', '-1'))
    except ValueError:
        revision = -1
    values, errors = {}, {}
    for name, field in form.fields.items():
        raw = request.POST.getlist(name)
        value = raw if isinstance(field, forms.MultipleChoiceField) else (raw[0] if raw else '')
        try:
            if not isinstance(field, forms.MultipleChoiceField) and len(raw) > 1:
                raise forms.ValidationError('El campo no puede repetirse.')
            # A decision can remain unchosen while the person writes a correction.
            if name != 'status' or value:
                field.clean(value)
            values[name] = raw
        except forms.ValidationError as error:
            errors[name] = error.messages
    if errors:
        return JsonResponse({'error': ' '.join(str(form.fields[name].label or name) + ': ' + ' '.join(messages) for name, messages in errors.items()), 'fields': errors}, status=400)
    if int(values['revision'][0]) != base_revision:
        return JsonResponse({'error': 'Este caso fue modificado por otra revisión. Tu avance sigue en esta pestaña; recargá el caso antes de confirmar.'}, status=409)
    with transaction.atomic():
        ReviewTask.objects.select_for_update().get(pk=root_id)
        draft, _ = ReviewDraft.objects.get_or_create(task_id=root_id, user_id=request.user.pk,
            defaults={'updated_at': timezone.now()})
        if revision != draft.revision:
            return JsonResponse({'error': 'El borrador cambió en otra pestaña. Tu avance sigue aquí; recargá antes de continuar.'}, status=409)
        data = dict(draft.data)
        items = dict(data.get('items', {}))
        query = {name: request.GET[name] for name in ('tab', 'case', 'step', 'version', 'article') if name in request.GET}
        query.update(tab='copy' if task.kind == 'indice' else ('topics' if task.model_result.get('category') == 'temas' else 'relations'), case=str(task.pk))
        items[key(task, control)] = {'task_id': task.pk, 'fields': values, 'query': query}
        draft.data = {**data, 'items': items, 'cursor': query}
        draft.revision += 1
        draft.updated_at = timezone.now()
        draft.save(update_fields=['data', 'revision', 'updated_at'])
    if request.headers.get('X-Review-Async'):
        return JsonResponse({'revision': draft.revision, 'count': len(items), 'saved_at': draft.updated_at.isoformat()})
    return redirect(request.get_full_path())


def summary(entry, root_id, user, draft):
    from .workflow import norm_sections
    _, tabs = norm_sections(entry, user)
    items = draft.data.get('items', {}) if draft else {}
    rows = []
    tasks = {task.pk: task for task in ReviewTask.objects.filter(norm_id=entry.norm_id, crawl_run_id=entry.crawl_run_id,superseded=False).select_related('norm', 'article_version__article', 'article_version__norm_version')}
    for tab in tabs.values():
        for case in tab['cases']:
            task = tasks[case['id']]
            if task.kind == 'indice':
                vote = NormReview.objects.filter(task=task, user_id=user.pk).first()
                copy = copy_review.copy_context(task, vote, SimpleNamespace(GET=QueryDict()), case['url'])
                for check in copy['checks']:
                    item = items.get(key(task, check))
                    rows.append({'label': check['label'], 'section': tab['label'], 'url': check['url'],
                                 'status': (item['fields'].get('status') or ['pendiente'])[0] or 'pendiente' if item else check['status'], 'automatic':check.get('proof',{}).get('status')=='coincide','draft': bool(item)})
            else:
                item = items.get(key(task))
                if not item and (task.decision != 'revision' or task.model_result.get('requires_processing')):
                    continue
                rows.append({'label': ('Propuesta temática de la norma' if task.model_result.get('scope')=='norma' else ('Artículo ' + case['article'] + ' · ' if case['article'] else '') + 'Relación propuesta'),
                             'section': tab['label'], 'url': case['url'],
                             'status': (item['fields'].get('status') or ['pendiente'])[0] or 'pendiente' if item else task.review_status, 'draft': bool(item)})
    labels = copy_review.STATUS_LABELS
    rows=[row for row in rows if row.get('draft') or not row.get('automatic')]
    for row in rows:
        row['status_label'] = labels[row['status']]
    return {'rows': rows, 'total': len(rows), 'done': sum(r['status'] != 'pendiente' for r in rows),
            'differences': sum(r['status'] == 'rechazada' for r in rows),
            'pending': sum(r['status'] == 'pendiente' for r in rows)}


def finalize(request, pk):
    from .workflow import CopyDecisionForm, norm_sections, record_decision, review_url
    if request.method != 'POST':
        return JsonResponse({'error': 'Se requiere confirmar la revisión.'}, status=405)
    entry = get_object_or_404(ReviewTask.objects.select_related('norm', 'crawl_run'), pk=pk)
    root_id, tabs = norm_sections(entry, request.user)
    allowed = {case['id'] for tab in tabs.values() for case in tab['cases']}
    error = None
    error_url = review_url(root_id, 'copy') + '&finish=1'
    with transaction.atomic():
        ReviewTask.objects.select_for_update().get(pk=root_id)
        draft = ReviewDraft.objects.filter(task_id=root_id, user_id=request.user.pk).first()
        if draft and str(draft.data.get('finalized_revision')) == request.POST.get('draft_revision') and not draft.data.get('items'):
            return redirect(review_url(root_id, 'copy') + '&finish=1&confirmed=1')
        if not draft or str(draft.revision) != request.POST.get('draft_revision'):
            error = ('El borrador cambió. Volvé al resumen para confirmar la versión actual.', 409)
        elif not draft.data.get('items'):
            error = ('No hay cambios nuevos para confirmar.', 400)
        else:
            tasks = {task.pk: task for task in ReviewTask.objects.select_for_update(of=('self',)).filter(pk__in=allowed).order_by('pk').select_related(
                'norm', 'article_version__article', 'article_version__norm_version', 'legal_change__amending_norm', 'legal_change__affected_norm', 'legal_change__affected_article')}
            prepared = []
            for item in draft.data['items'].values():
                task = tasks.get(item['task_id'])
                if not task:
                    raise Http404
                data = fields_data(item['fields'])
                data['status'] = data.get('status') or 'pendiente'
                vote = NormReview.objects.filter(task=task, user_id=request.user.pk).first() if task.kind == 'indice' else None
                revision = (vote.revision if vote else 0) if task.kind == 'indice' else task.revision
                query = QueryDict(urlencode(item['query']))
                url = review_url(root_id, item['query']['tab'], task.pk)
                copy = copy_review.copy_context(task, vote, SimpleNamespace(GET=query), url) if task.kind == 'indice' else None
                passage = passage_review.passage_context(task, SimpleNamespace(GET=query), url) if task.kind == 'pasaje' else None
                control = copy['control'] if copy else None
                form = CopyDecisionForm(data, control=control) if control else passage_review.proposal_form(task, data, {})
                if not form.is_valid():
                    error = ('Hay una decisión incompleta o contradictoria. Corregila antes de finalizar.', 400)
                    error_url = url + '&' + query.urlencode() + '&validate=1'
                    break
                if form.cleaned_data['revision'] != revision:
                    error = ('Un caso cambió desde que empezaste. Revisá el resultado actualizado antes de finalizar.', 409)
                    error_url = url + '&' + query.urlencode() + '&validate=1'
                    break
                prepared.append((task, form, copy, passage, control))
            if not error:
                for task, form, copy, passage, control in prepared:
                    vote = NormReview.objects.filter(task=task, user_id=request.user.pk).first() if control else None
                    revision = (vote.revision if vote else 0) if control else task.revision
                    record_decision(task, request.user, form, copy, passage, control, bool(control), bool(passage), vote, revision)
                draft.data = {'cursor': draft.data.get('cursor', {}), 'finalized_revision': draft.revision}
                draft.revision += 1
                draft.updated_at = timezone.now()
                draft.save(update_fields=['data', 'revision', 'updated_at'])
    if error:
        if request.headers.get('X-Review-Async'):
            return JsonResponse({'error': error[0], 'url': error_url}, status=error[1])
        return render(request, 'review/finalize_error.html', {'error': error[0], 'url': error_url}, status=error[1])
    return redirect(review_url(root_id, 'copy') + '&finish=1&confirmed=1')
