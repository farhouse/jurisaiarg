from functools import wraps
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import BooleanField, Case, Count, Exists, OuterRef, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from .forms import DecisionForm
from .models import CrawlJob, CrawlRun, LegalChange, NormReview, ReviewDraft, ReviewEvent, ReviewTask, NormReference
from . import copy_review, passage_review, draft_review, dossier


def validator_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.groups.filter(name="Validadores").exists()):
            raise PermissionDenied("Tu cuenta necesita el rol de validador.")
        return view(request, *args, **kwargs)
    return wrapped


def selected_run(request):
    value = request.GET.get("run")
    if value:
        if not value.isdigit():
            raise Http404
        return get_object_or_404(CrawlRun, pk=int(value))
    # Una descarga nueva no debe ocultar el lote que los validadores están revisando.
    return CrawlRun.objects.filter(pk__in=ReviewTask.objects.values("crawl_run_id")).first() or CrawlRun.objects.first()


class CopyDecisionForm(DecisionForm):
    check = forms.ChoiceField(widget=forms.HiddenInput)
    issues = forms.MultipleChoiceField(choices=copy_review.ISSUES, required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, control, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['check'].choices = [(control['key'], control['label'])]
        self.fields['status'].choices = [('validada', 'Sí, coincide'), ('rechazada', 'Hay diferencias'), ('pendiente', 'No puedo comprobarlo')]
        self.control = control

    def clean(self):
        data = super().clean()
        if data.get('status') == 'validada':
            if self.control.get('scope')=='document' and self.control['proof']['status']=='diferencias' and not data.get('notes'):
                self.add_error('notes','El control automático encontró diferencias. Explicá por qué la copia completa es correcta o señalá el problema.')
            if not self.control['source']:
                self.add_error('status', 'Falta la fuente de este lote. Dejá pendiente este control hasta poder compararlo.')
            if data.get('issues'):
                self.add_error('issues', 'Marcaste problemas: elegí «Hay diferencias» o quitá esas marcas.')
        return data


def guide(request):
    return render(request, "review/guide.html")


def personal_tasks(tasks, user):
    roots=list(tasks.filter(kind='indice',model_result__copy_policy='document-integrity-v2').values('pk','norm_id','model_result'))
    previous={}
    own={}
    for vote in NormReview.objects.filter(task__norm_id__in={r['norm_id'] for r in roots},user_id=user.pk).order_by('-updated_at').values('task_id','task__norm_id','checks'):
        own[vote['task_id']]=vote['checks']
        for key,check in vote['checks'].items():
            if check.get('scope')=='document':
                previous.setdefault((vote['task__norm_id'],key),check)
    completed=[]
    for root in roots:
        current=[]
        for doc in root['model_result'].get('documents',[]):
            key=doc['integrity'].get('key')
            check=own.get(root['pk'],{}).get(key) or previous.get((root['norm_id'],key),{})
            current.append((doc['integrity']['status'],check.get('status') in ('validada','rechazada')))
        if any(done for _,done in current) and all(done for status,done in current if status=='diferencias'):
            completed.append(root['pk'])
    return tasks.annotate(my_completed=Case(
        When(kind='indice',model_result__copy_policy='document-integrity-v2',then=Case(When(pk__in=completed,then=Value(True)),default=Value(False),output_field=BooleanField())),
        default=Exists(NormReview.objects.filter(task_id=OuterRef('pk'),user_id=user.pk).exclude(status='pendiente')),
        output_field=BooleanField()))


def pending_tasks(tasks, user):
    return personal_tasks(tasks, user).filter(
        Q(kind="indice", my_completed=False) | Q(kind="pasaje", review_status="pendiente"))


@validator_required
def queue(request):
    run = selected_run(request)
    base = ReviewTask.objects.filter(crawl_run=run,superseded=False)
    tasks = personal_tasks(base.select_related("norm"), request.user)
    scope = request.GET.get("scope", "queue")
    if scope == "queue":
        tasks = pending_tasks(tasks, request.user).filter(decision="revision")
    elif scope == "automatic":
        tasks = tasks.filter(review_status="pendiente").exclude(decision="revision")
    elif scope == "done":
        tasks = tasks.filter(Q(kind="indice", votes__status__in=["validada", "rechazada"]) | Q(kind="pasaje") & ~Q(review_status="pendiente")).distinct()
    elif scope == "mine":
        tasks = tasks.filter(Q(kind="indice", my_completed=True) | Q(kind="pasaje", reviewed_by=request.user.get_username()) & ~Q(review_status="pendiente"))
    elif scope != "all":
        raise Http404
    # A norm dossier is the work unit; fragments remain internal evidence.
    modern = ReviewTask.objects.filter(crawl_run=run,kind='indice',model_result__copy_policy='document-integrity-v2').values('norm_id')
    modern_ids=set(modern.values_list('norm_id',flat=True))
    kind = request.GET.get("kind", "")
    if kind in ("indice", "pasaje"):
        tasks = tasks.filter(kind=kind)
    grouped = tasks.filter(norm_id__in=modern).order_by('norm_id','kind','id').distinct('norm_id').values('id')
    tasks = tasks.filter(Q(norm_id__in=modern,pk__in=grouped)|~Q(norm_id__in=modern))
    query = request.GET.get("q", "").strip()
    if query:
        condition = Q(norm__number__icontains=query) | Q(norm__canonical_title__icontains=query)
        if query.isdigit():
            condition |= Q(norm__infoleg_id=int(query))
        tasks = tasks.filter(condition)
    page = Paginator(tasks.order_by("kind", "id"), 20).get_page(request.GET.get("page"))
    own_votes = {vote.task_id: vote for vote in NormReview.objects.filter(task_id__in=[task.pk for task in page], user_id=request.user.pk)}
    for task in page:
        task.is_dossier=task.norm_id in modern_ids
        if task.kind == "indice":
            vote = own_votes.get(task.pk)
            task.review_status = vote.status if vote else "pendiente"
            task.reviewed_by = vote.username if vote else None
            task.reviewed_at = vote.updated_at if vote else None
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "review/queue.html", {
        "run": run, "runs": CrawlRun.objects.all()[:30], "page": page,
        "scope": scope, "kind": kind, "query": query, "params": params.urlencode(),
        "pending": work_count(pending_tasks(base, request.user).filter(decision="revision"),modern),
        "my_count": NormReview.objects.filter(task__crawl_run=run, user_id=request.user.pk).exclude(status="pendiente").count() + base.filter(kind="pasaje", reviewed_by=request.user.get_username()).exclude(review_status="pendiente").count(),
        "automatic": work_count(base.exclude(decision="revision").filter(review_status="pendiente"),modern),
    })


def work_count(tasks, modern):
    return tasks.filter(norm_id__in=modern).values('norm_id').distinct().count()+tasks.exclude(norm_id__in=modern).count()


def review_url(root_id, tab, case=None):
    params = {'tab': tab}
    if case is not None:
        params['case'] = case
    return reverse('review:task', args=[root_id]) + '?' + urlencode(params)


def norm_sections(entry, user):
    # Solo metadatos para navegar: el texto completo se carga al abrir cada caso.
    rows = list(ReviewTask.objects.filter(norm_id=entry.norm_id, crawl_run_id=entry.crawl_run_id,superseded=False)
                .order_by('id').values('id', 'kind', 'model_result__category', 'review_status',
                                      'article_version__article__article_label', 'article_version__norm_version__version_type'))
    votes = dict(NormReview.objects.filter(task_id__in=[row['id'] for row in rows if row['kind'] == 'indice'],
                                          user_id=user.pk).values_list('task_id', 'status'))
    root_id = next((row['id'] for row in rows if row['kind'] == 'indice'), entry.pk)
    tabs = {key: {'key': key, 'label': label, 'cases': [], 'approved': 0, 'rejected': 0, 'pending': 0,
                  'url': review_url(root_id, key)}
            for key, label in [('copy', 'Copia de la norma'), ('topics', 'Materias y tags'), ('relations', 'Relaciones')]}
    labels = dict(ReviewTask._meta.get_field('review_status').choices)
    for row in rows:
        key = 'copy' if row['kind'] == 'indice' else ('topics' if row['model_result__category'] == 'temas' else 'relations')
        status = votes.get(row['id'], 'pendiente') if key == 'copy' else row['review_status']
        tab = tabs[key]
        tab['cases'].append({'id': row['id'], 'status': status, 'status_label': labels[status],
                             'article': row['article_version__article__article_label'], 'version': row['article_version__norm_version__version_type'],
                             'url': review_url(root_id, key, row['id'])})
        tab[{'validada': 'approved', 'rechazada': 'rejected', 'pendiente': 'pending'}[status]] += 1
    for tab in tabs.values():
        tab['total'] = len(tab['cases'])
        tab['reviewed'] = tab['approved'] + tab['rejected']
    return root_id, tabs


def record_decision(locked, user, form, copy, passage, control, guided_post, proposal_post, vote, revision):
    now = timezone.now()
    status = form.cleaned_data["status"]
    notes = form.cleaned_data["notes"].strip()
    previous = (vote.status if vote else "pendiente") if locked.kind == "indice" else locked.review_status
    checks = dict(vote.checks) if vote else {}
    details = {}
    event_notes = notes
    if proposal_post:
        proposed = {**form.correction, 'issues': form.cleaned_data['issues']}
        details = {'proposal_kind': form.proposal_kind, 'original': form.original,
                   'proposed': proposed, 'changes': form.changes,
                   'sources': [{'id': v['source'].pk, 'norm_id': v['source'].norm_id,
                                'run_id': v['source'].crawl_run_id, 'retrieved_at': v['source'].retrieved_at.isoformat()}
                               for v in passage['viewers']]}
        event_notes = notes or '; '.join(form.changes) or ', '.join(dict(form.fields['issues'].choices)[issue] for issue in form.cleaned_data['issues'])
        if not notes:
            notes = event_notes
    if guided_post:
        issues = form.cleaned_data['issues']
        if not notes and issues:
            notes = ', '.join(dict(copy_review.ISSUES)[key] for key in issues)
        check_result = {'status': status, 'notes': notes, 'issues': issues,
                        'source_id': control['source'].pk if control['source'] else None,
                        'label': control['label'], 'updated_at': now.isoformat(),
                        **({'scope':'document','version':control['version'],'source_hash':control['proof'].get('source_hash'), 'extracted_hash':control['proof'].get('extracted_hash')} if control.get('scope')=='document' else {})}
        checks[control['key']] = check_result
        details = {'check': control['key'], **check_result}
        event_notes = control['label'] + ': ' + (notes or copy_review.STATUS_LABELS[status])
        status = copy_review.aggregate(copy['checks'], checks)
        notes = '\n'.join(c['label'] + ': ' + checks[c['key']]['notes'] for c in copy['checks']
                          if c['key'] in checks and checks[c['key']]['notes'])
    elif locked.kind == 'indice':
        # Una decisión global antigua reemplaza el desglose anterior, si existía.
        checks = {}
    ReviewEvent.objects.create(task=locked, username=user.get_username(), previous_status=previous, status=status, notes=event_notes, details=details, created_at=now)
    if locked.kind == "indice":
        NormReview.objects.update_or_create(task=locked, user_id=user.pk, defaults={"username": user.get_username(), "status": status, "notes": notes, 'checks': checks, "revision": revision + 1, "updated_at": now})
    else:
        if proposal_post:
            locked.proposed_correction = proposed
        locked.review_status, locked.review_notes = status, notes
        locked.reviewed_by = user.get_username() if status != "pendiente" else None
        locked.reviewed_at = now if status != "pendiente" else None
        locked.revision += 1
        locked.save(update_fields=["review_status", "review_notes", "reviewed_by", "reviewed_at", "revision", 'proposed_correction'])
        if locked.reference_id:
            NormReference.objects.filter(pk=locked.reference_id).update(review_status=status,review_notes=notes,reviewed_by=locked.reviewed_by,reviewed_at=locked.reviewed_at)
        if locked.legal_change_id:
            LegalChange.objects.filter(pk=locked.legal_change_id).update(review_status=status, review_notes=notes, reviewed_by=locked.reviewed_by, reviewed_at=locked.reviewed_at, updated_at=now)


def focus_tabs(tabs, entry, root_id, focus, version):
    for tab in tabs.values():
        tab['url'] = review_url(root_id, tab['key'])
        tab['all_url'] = tab['url']
        if focus:
            tab['url'] += '&' + urlencode({'focus_article': focus, 'focus_version': version or ''})
            if tab['key'] == 'copy':
                index = next((i for i, a in enumerate(a for a in copy_review.articles_for(entry if entry.kind == 'indice' else ReviewTask.objects.get(pk=root_id)) if a['version'] == version) if a['label'] == focus), None)
                if index is not None:
                    tab['url'] += '&' + urlencode({'step': 'articles', 'article': index, 'version': version})


@validator_required
def task_detail(request, pk):
    entry = get_object_or_404(ReviewTask.objects.select_related('norm', 'crawl_run'), pk=pk)
    if entry.superseded:
        root = ReviewTask.objects.filter(norm_id=entry.norm_id,crawl_run_id=entry.crawl_run_id,kind='indice').first()
        if root:
            return redirect(reverse('review:task',args=[root.pk]))
    root_id, tabs = norm_sections(entry, request.user)
    draft = ReviewDraft.objects.filter(task_id=root_id, user_id=request.user.pk).first()
    if request.method == 'GET' and entry.pk == root_id and not request.GET and draft and draft.data.get('items') and draft.data.get('cursor'):
        return redirect(reverse('review:task', args=[root_id]) + '?' + urlencode(draft.data['cursor']))
    modern = ReviewTask.objects.filter(pk=root_id,model_result__copy_policy='document-integrity-v2').exists()
    default_tab = 'dossier' if modern else 'copy' if entry.kind == 'indice' else ('topics' if entry.model_result.get('category') == 'temas' else 'relations')
    tab_key = request.GET.get('tab', default_tab)
    if modern and tab_key=='dossier':
        if request.method!='GET':raise Http404
        draft_count=len(draft.data.get('items',{})) if draft else 0
        data=dossier.context(entry,root_id,request)
        return render(request,'review/task.html',{'norm':entry.norm,'run':entry.crawl_run,'root_id':root_id,'task':data['root'],
            'dossier':data,'modern':True,'tabs':tabs.values(),'draft_count':draft_count,'draft_revision':draft.revision if draft else 0,
            'finish_url':review_url(root_id,'copy')+'&finish=1'})
    if tab_key not in tabs:
        raise Http404
    active_tab = tabs[tab_key]
    cases = active_tab['cases']
    selected = request.GET.get('case')
    if selected is not None:
        try:
            selected_id = int(selected)
        except ValueError:
            raise Http404
        current = next((case for case in cases if case['id'] == selected_id), None)
        if current is None:
            raise Http404  # Nunca abrir ni modificar un caso de otra norma, lote o solapa.
    elif request.GET.get('focus_article') and tab_key != 'copy':
        current = next((case for case in cases if case['article'] == request.GET['focus_article']
                        and (not request.GET.get('focus_version') or case['version'] == request.GET['focus_version'])), None)
    elif 'tab' not in request.GET:
        current = next(case for case in cases if case['id'] == entry.pk)
    else:
        current = next((case for case in cases if case['status'] == 'pendiente'), cases[0] if cases else None)
    job = CrawlJob.objects.filter(crawl_run_id=entry.crawl_run_id, mode__in=['full', 'process']).order_by('-id').first()
    context = {'norm': entry.norm, 'run': entry.crawl_run, 'root_id': root_id,
               'tabs': tabs.values(), 'active_tab': active_tab, 'section_cases': cases, 'processing_job': job,
               'modern':modern, 'draft_revision': draft.revision if draft else 0, 'draft_count': len(draft.data.get('items', {})) if draft else 0,
               'finish_url': review_url(root_id, 'copy') + '&finish=1', 'focused_article': request.GET.get('focus_article', '')}
    if request.GET.get('finish') == '1':
        context.update(finish_summary=draft_review.summary(entry, root_id, request.user, draft), task=entry, confirmed=request.GET.get('confirmed') == '1' and bool(draft and 'finalized_revision' in draft.data and not draft.data.get('items')))
        return render(request, 'review/task.html', context)
    if current is None:
        focus_tabs(tabs, entry, root_id, request.GET.get('focus_article'), request.GET.get('focus_version'))
        if request.method == 'POST':
            raise Http404
        return render(request, 'review/task.html', context)
    task = get_object_or_404(ReviewTask.objects.select_related('norm', 'article_version__norm_version',
                              'article_version__article', 'legal_change__amending_norm', 'legal_change__affected_norm', 'legal_change__affected_article','reference__source_norm','reference__target_norm','reference__source_article_version__article'), pk=current['id'])
    position = cases.index(current)
    context.update(case_position=position + 1, previous_case=cases[position - 1] if position else None,
                   next_case=cases[position + 1] if position + 1 < len(cases) else None,
                   current_url=current['url'])
    own_vote = NormReview.objects.filter(task=task, user_id=request.user.pk).first() if task.kind == "indice" else None
    if task.kind == "indice":
        task.review_status = own_vote.status if own_vote else "pendiente"
        task.review_notes = own_vote.notes if own_vote else ""
        task.reviewed_by = own_vote.username if own_vote else None
        task.reviewed_at = own_vote.updated_at if own_vote else None
    initial = {"status": task.review_status if task.review_status != "pendiente" else None, "notes": task.review_notes, "revision": (own_vote.revision if own_vote else 0) if task.kind == "indice" else task.revision}
    copy = copy_review.copy_context(task, own_vote, request, current['url']) if task.kind == 'indice' else None
    passage = passage_review.passage_context(task, request, current['url']) if task.kind == 'pasaje' else None
    draft_review.preview(copy, task, draft)
    control = copy['control'] if copy else None
    focus = control['article']['label'] if control and control.get('article') else (task.article_version.article.article_label if task.article_version_id else request.GET.get('focus_article'))
    version = copy['version'] if copy else (task.article_version.norm_version.version_type if task.article_version_id else request.GET.get('focus_version'))
    focus_tabs(tabs, entry, root_id, focus, version)
    guided_post = request.method == 'POST' and 'check' in request.POST
    proposal_post = request.method == 'POST' and 'proposal_kind' in request.POST
    if copy and control and (request.method != 'POST' or guided_post):
        form = CopyDecisionForm(request.POST if guided_post else None, control=control, initial={
            **initial, 'check': control['key'], 'status': control['status'] if control['saved'] else None,
            'notes': control['notes'], 'issues': control['issues']})
    elif passage and (request.method != 'POST' or proposal_post):
        form = passage_review.proposal_form(task, request.POST if proposal_post else None, initial)
    else:
        form = DecisionForm(request.POST if request.method == 'POST' else None, initial=initial)
    if modern and request.method=='POST' and not guided_post and not proposal_post:
        raise Http404  # A legacy identity/global form cannot approve a new document scope.
    if request.method == 'POST' and request.POST.get('draft') == '1':
        if not (guided_post and control or proposal_post and passage):
            raise Http404
        return draft_review.save(request, root_id, task, form, control, initial['revision'])
    if request.method == 'GET':
        draft_item = draft.data.get('items', {}).get(draft_review.key(task, control)) if draft else None
        if draft_item and draft_item['fields'].get('revision') != [str(initial['revision'])]:
            context.update(conflict_revision=initial['revision'], conflict_status=task.get_review_status_display(), conflict_notes=task.review_notes)
        draft_review.overlay(form, draft_item)
        if draft_item and request.GET.get('validate') == '1':
            data = draft_review.fields_data(draft_item['fields'])
            data['status'] = data.get('status') or 'pendiente'
            form = CopyDecisionForm(data, control=control, initial=initial) if control else passage_review.proposal_form(task, data, initial)
    code = 200
    if request.method == "POST":
        if guided_post and (not copy or not control):
            raise Http404
        if proposal_post and not passage:
            raise Http404
        if form.is_valid():
            with transaction.atomic():
                locked = ReviewTask.objects.select_for_update().get(pk=task.pk)
                vote = NormReview.objects.filter(task=locked, user_id=request.user.pk).first() if locked.kind == "indice" else None
                revision = (vote.revision if vote else 0) if locked.kind == "indice" else locked.revision
                if revision != form.cleaned_data["revision"]:
                    form.add_error(None, "Esta revisión cambió desde que abriste el formulario. Recargá el caso antes de continuar.")
                    code = 409
                else:
                    record_decision(locked, request.user, form, copy, passage, control, guided_post, proposal_post, vote, revision)
            if code != 409:
                messages.success(request, "Decisión guardada. Quedó registrada con tu usuario y fecha.")
                if guided_post:
                    return redirect(copy['next_url'] if request.POST.get('continue') else control['url'])
                if request.POST.get('continue'):
                    _, updated_tabs = norm_sections(task, request.user)
                    order = [tab_key] + [key for key in tabs if key != tab_key]
                    for key in order:
                        next_case = next((case for case in updated_tabs[key]['cases']
                                          if case['status'] == 'pendiente' and case['id'] != task.pk), None)
                        if next_case:
                            return redirect(next_case['url'])
                return redirect(current['url'])
        elif code == 200:
            code = 400
    articles = copy_review.articles_for(task) if task.kind == 'indice' else []
    context.update(task=task, form=form, articles=articles,
                   copy=copy, passage=passage, snapshot='articles' in task.model_result, history=task.events.order_by('-id')[:10])
    return render(request, 'review/task.html', context, status=code)


@validator_required
@require_safe
def copy_source(request, pk, source_id):
    task = get_object_or_404(ReviewTask.objects.select_related('legal_change'), pk=pk)
    return copy_review.source_response(task, source_id, request.GET.get('focus', ''))


STATUS_LABELS = {
    "completa": "Texto actualizado", "solo_texto_original": "Texto original",
    "solo_ficha": "Solo ficha", "vacia": "Sin ficha reconocible",
    "error_descarga": "Error de descarga", "error_parseo": "Error de extracción",
    "procesando": "En descarga", "pendiente": "Pendiente",
}


def counted_indices(run):
    return ReviewTask.objects.filter(crawl_run=run, kind="indice").select_related("norm").annotate(
        approved=Count("votes", filter=Q(votes__status="validada")),
        rejected=Count("votes", filter=Q(votes__status="rechazada")),
        pending_votes=Count("votes", filter=Q(votes__status="pendiente")),
        reviewers_count=Count("votes"),
    ).order_by("norm__infoleg_id", "pk")


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied("Esta página es para administradores.")
        return view(request, *args, **kwargs)
    return wrapped


@staff_required
def management(request):
    run = selected_run(request)
    indices = counted_indices(run)
    laws = indices
    query = request.GET.get("q", "").strip()
    if query:
        condition = Q(norm__number__icontains=query) | Q(norm__canonical_title__icontains=query)
        if query.isdigit():
            condition |= Q(norm__infoleg_id=int(query))
        laws = laws.filter(condition)
    status = request.GET.get("status", "")
    filters = {
        "validada": Q(approved__gt=0), "rechazada": Q(rejected__gt=0),
        "pendiente": Q(pending_votes__gt=0), "sin_revision": Q(reviewers_count=0),
        "desacuerdo": Q(approved__gt=0, rejected__gt=0),
    }
    if status:
        if status not in filters:
            raise Http404
        laws = laws.filter(filters[status])
    page = Paginator(laws, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    tasks = ReviewTask.objects.filter(crawl_run=run,superseded=False)
    return render(request, "review/management.html", {
        "run": run, "runs": CrawlRun.objects.all()[:30], "page": page,
        "query": query, "status": status, "params": params.urlencode(),
        "law_count": indices.count(),
        "unreviewed": indices.filter(reviewers_count=0).count(),
        "disagreements": indices.filter(approved__gt=0, rejected__gt=0).count(),
        "vote_count": NormReview.objects.filter(task__crawl_run=run, task__kind="indice").exclude(status="pendiente").count(),
        "downloads": [{"label": STATUS_LABELS.get(row["status"], row["status"]), "total": row["total"]}
                      for row in (run.items.values("status").annotate(total=Count("id")).order_by("status") if run else [])],
        "passages": tasks.filter(kind="pasaje").count(),
        "pending_passages": tasks.filter(kind="pasaje", decision="revision", review_status="pendiente").count(),
        "automatic": tasks.filter(kind="pasaje").exclude(decision="revision").filter(review_status="pendiente").count(),
    })


@staff_required
def law_reviews(request, pk):
    task = get_object_or_404(ReviewTask.objects.select_related("norm", "crawl_run"), pk=pk, kind="indice")
    indices = counted_indices(task.crawl_run)
    task = indices.get(pk=pk)
    status = request.GET.get("status", "")
    if status not in ("", "validada", "rechazada", "pendiente"):
        raise Http404
    reviews = task.votes.order_by("-updated_at", "pk")
    if status:
        reviews = reviews.filter(status=status)
    page = Paginator(reviews, 20).get_page(request.GET.get("review_page"))
    default_page = indices.filter(norm__infoleg_id__lt=task.norm.infoleg_id).count() // 20 + 1
    sidebar = Paginator(indices, 20).get_page(request.GET.get("page", default_page))
    return render(request, "review/law_reviews.html", {
        "task": task, "run": task.crawl_run, "status": status,
        "page": page, "sidebar": sidebar,
        "history": task.events.order_by("-id")[:20],
    })
