from functools import wraps
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import ArticleVersion, CrawlJob, CrawlRun, LegalChange, NormReview, ReviewEvent, ReviewTask


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


class DecisionForm(forms.Form):
    status = forms.ChoiceField(choices=[("validada", "Correcto"), ("rechazada", "Incorrecto"), ("pendiente", "Dejar pendiente")], widget=forms.RadioSelect)
    notes = forms.CharField(required=False, max_length=5000, label="Observaciones", widget=forms.Textarea(attrs={"rows": 4}))
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        if data.get("status") == "rechazada" and not data.get("notes", "").strip():
            self.add_error("notes", "Indicá qué está mal y qué habría que corregir.")
        return data


def guide(request):
    return render(request, "review/guide.html")


def personal_tasks(tasks, user):
    return tasks.annotate(my_completed=Exists(NormReview.objects.filter(
        task_id=OuterRef("pk"), user_id=user.pk).exclude(status="pendiente")))


def pending_tasks(tasks, user):
    return personal_tasks(tasks, user).filter(
        Q(kind="indice", my_completed=False) | Q(kind="pasaje", review_status="pendiente"))


@validator_required
def queue(request):
    run = selected_run(request)
    base = ReviewTask.objects.filter(crawl_run=run)
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
    kind = request.GET.get("kind", "")
    if kind in ("indice", "pasaje"):
        tasks = tasks.filter(kind=kind)
    query = request.GET.get("q", "").strip()
    if query:
        condition = Q(norm__number__icontains=query) | Q(norm__canonical_title__icontains=query)
        if query.isdigit():
            condition |= Q(norm__infoleg_id=int(query))
        tasks = tasks.filter(condition)
    page = Paginator(tasks.order_by("kind", "id"), 20).get_page(request.GET.get("page"))
    own_votes = {vote.task_id: vote for vote in NormReview.objects.filter(task_id__in=[task.pk for task in page], user_id=request.user.pk)}
    for task in page:
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
        "pending": pending_tasks(base, request.user).filter(decision="revision").count(),
        "my_count": NormReview.objects.filter(task__crawl_run=run, user_id=request.user.pk).exclude(status="pendiente").count() + base.filter(kind="pasaje", reviewed_by=request.user.get_username()).exclude(review_status="pendiente").count(),
        "automatic": base.exclude(decision="revision").filter(review_status="pendiente").count(),
    })


def review_url(root_id, tab, case=None):
    params = {'tab': tab}
    if case is not None:
        params['case'] = case
    return reverse('review:task', args=[root_id]) + '?' + urlencode(params)


def norm_sections(entry, user):
    # Solo metadatos para navegar: el texto completo se carga al abrir cada caso.
    rows = list(ReviewTask.objects.filter(norm_id=entry.norm_id, crawl_run_id=entry.crawl_run_id)
                .order_by('id').values('id', 'kind', 'model_result__category', 'review_status',
                                      'article_version__article__article_label'))
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
                             'article': row['article_version__article__article_label'],
                             'url': review_url(root_id, key, row['id'])})
        tab[{'validada': 'approved', 'rechazada': 'rejected', 'pendiente': 'pending'}[status]] += 1
    for tab in tabs.values():
        tab['total'] = len(tab['cases'])
        tab['reviewed'] = tab['approved'] + tab['rejected']
    return root_id, tabs


@validator_required
def task_detail(request, pk):
    entry = get_object_or_404(ReviewTask.objects.select_related('norm', 'crawl_run'), pk=pk)
    root_id, tabs = norm_sections(entry, request.user)
    default_tab = 'copy' if entry.kind == 'indice' else ('topics' if entry.model_result.get('category') == 'temas' else 'relations')
    tab_key = request.GET.get('tab', default_tab)
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
    elif 'tab' not in request.GET:
        current = next(case for case in cases if case['id'] == entry.pk)
    else:
        current = next((case for case in cases if case['status'] == 'pendiente'), cases[0] if cases else None)
    job = CrawlJob.objects.filter(crawl_run_id=entry.crawl_run_id, mode__in=['full', 'process']).order_by('-id').first()
    context = {'norm': entry.norm, 'run': entry.crawl_run, 'root_id': root_id,
               'tabs': tabs.values(), 'active_tab': active_tab, 'section_cases': cases, 'processing_job': job}
    if current is None:
        if request.method == 'POST':
            raise Http404
        return render(request, 'review/task.html', context)
    task = get_object_or_404(ReviewTask.objects.select_related('norm', 'article_version__norm_version',
                              'legal_change__amending_norm'), pk=current['id'])
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
    form = DecisionForm(initial={"status": task.review_status if task.review_status != "pendiente" else None, "notes": task.review_notes, "revision": (own_vote.revision if own_vote else 0) if task.kind == "indice" else task.revision})
    code = 200
    if request.method == "POST":
        form = DecisionForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                locked = ReviewTask.objects.select_for_update().get(pk=task.pk)
                vote = NormReview.objects.filter(task=locked, user_id=request.user.pk).first() if locked.kind == "indice" else None
                revision = (vote.revision if vote else 0) if locked.kind == "indice" else locked.revision
                if revision != form.cleaned_data["revision"]:
                    form.add_error(None, "Esta revisión cambió desde que abriste el formulario. Recargá el caso antes de continuar.")
                    code = 409
                else:
                    now = timezone.now()
                    status = form.cleaned_data["status"]
                    notes = form.cleaned_data["notes"].strip()
                    previous = (vote.status if vote else "pendiente") if locked.kind == "indice" else locked.review_status
                    ReviewEvent.objects.create(task=locked, username=request.user.get_username(), previous_status=previous, status=status, notes=notes, created_at=now)
                    if locked.kind == "indice":
                        NormReview.objects.update_or_create(task=locked, user_id=request.user.pk, defaults={"username": request.user.get_username(), "status": status, "notes": notes, "revision": revision + 1, "updated_at": now})
                    else:
                        locked.review_status, locked.review_notes = status, notes
                        locked.reviewed_by = request.user.get_username() if status != "pendiente" else None
                        locked.reviewed_at = now if status != "pendiente" else None
                        locked.revision += 1
                        locked.save(update_fields=["review_status", "review_notes", "reviewed_by", "reviewed_at", "revision"])
                        if locked.legal_change_id:
                            LegalChange.objects.filter(pk=locked.legal_change_id).update(review_status=status, review_notes=notes, reviewed_by=locked.reviewed_by, reviewed_at=locked.reviewed_at, updated_at=now)
            if code != 409:
                messages.success(request, "Decisión guardada. Quedó registrada con tu usuario y fecha.")
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
    articles = task.model_result.get('articles', [])
    if task.kind == 'indice' and 'articles' not in task.model_result:
        articles = [{'label': av.article.article_label, 'version': av.norm_version.version_type,
                     'text': av.text_normalized} for av in ArticleVersion.objects.filter(
                         article__norm=task.norm).select_related('article', 'norm_version').order_by('norm_version_id','article__ordinal')]
    context.update(task=task, form=form, articles=articles,
                   snapshot='articles' in task.model_result, history=task.events.order_by('-id')[:10])
    return render(request, 'review/task.html', context, status=code)


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
    tasks = ReviewTask.objects.filter(crawl_run=run)
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
