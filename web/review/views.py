from decimal import Decimal, InvalidOperation
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ReviewForm
from .legal_search import parse_legal_query
from .models import Article, ArticleVersion, LegalChange, Norm
from .workflow import validator_required


def normalized_article_label(value):
    return re.sub(r"[^0-9a-z]", "", (value or "").lower())


def resolve_amending_article(change):
    if not change.amending_norm_id or not change.amending_article_reference:
        return None, None
    expected = normalized_article_label(change.amending_article_reference)
    candidates = Article.objects.filter(norm_id=change.amending_norm_id).prefetch_related(
        Prefetch(
            "articleversion_set",
            queryset=ArticleVersion.objects.select_related("norm_version").order_by(
                "-norm_version__is_current", "-norm_version_id",
            ),
        ),
    )
    article = next(
        (candidate for candidate in candidates if normalized_article_label(candidate.article_label) == expected),
        None,
    )
    if article is None:
        return None, None
    versions = list(article.articleversion_set.all())
    return article, versions[0] if versions else None


@login_required
def norm_search(request):
    query = request.GET.get("q", "").strip()
    norms = Norm.objects.all()
    parsed_query = parse_legal_query(query)
    if query:
        if parsed_query.action or parsed_query.article:
            changes = Q(changes_received__review_status__in=["pendiente", "validada"])
            if parsed_query.action:
                changes &= Q(changes_received__change_type=parsed_query.action)
            if parsed_query.article:
                changes &= Q(changes_received__affected_article__article_label__iexact=parsed_query.article)
            norms = norms.filter(changes)
        if parsed_query.text:
            search_query = SearchQuery(parsed_query.text, search_type="websearch", config="spanish")
            norms = norms.annotate(
                search_rank=SearchRank("search_chunks__search_vector", search_query),
            ).filter(
                Q(search_chunks__search_vector=search_query)
                | Q(number__icontains=parsed_query.text)
                | Q(canonical_title__icontains=parsed_query.text)
                | Q(popular_name__icontains=parsed_query.text)
                | Q(subject__icontains=parsed_query.text)
                | Q(summary__icontains=parsed_query.text)
            ).order_by("-search_rank", "infoleg_id")
        else:
            norms = norms.order_by("infoleg_id")
        norms = norms.distinct()
    else:
        norms = norms.exclude(index_status__in=["vacia", "pendiente"]).order_by("infoleg_id")
    page = Paginator(norms, 20).get_page(request.GET.get("page"))
    return render(request, "review/norm_search.html", {"page": page, "query": query, "parsed_query": parsed_query})


@login_required
def norm_detail(request, pk):
    current_versions = ArticleVersion.objects.select_related("norm_version").order_by(
        "article_id", "-norm_version__is_current", "-norm_version_id",
    )
    norm = get_object_or_404(
        Norm.objects.prefetch_related(
            "dates",
            Prefetch("articles__articleversion_set", queryset=current_versions),
            Prefetch(
                "changes_received",
                queryset=LegalChange.objects.filter(review_status="validada").select_related("amending_norm", "affected_article"),
            ),
            Prefetch(
                "changes_made",
                queryset=LegalChange.objects.filter(review_status="validada").select_related("affected_norm", "affected_article"),
            ),
        ),
        pk=pk,
    )
    articles = []
    for article in norm.articles.all().order_by("ordinal", "id"):
        versions = list(article.articleversion_set.all())
        if versions:
            article.display_version = next((v for v in versions if v.norm_version.is_current), versions[0])
            articles.append(article)
    return render(request, "review/norm_detail.html", {"norm": norm, "articles": articles})


def filtered_changes(request):
    queryset = LegalChange.objects.select_related(
        "affected_norm", "amending_norm", "affected_article", "source_article_version",
    )
    status = request.GET.get("status", "pendiente")
    change_type = request.GET.get("type", "")
    scope = request.GET.get("scope", "")
    query = request.GET.get("q", "").strip()
    min_confidence = request.GET.get("min_confidence", "")

    if status and status != "todos":
        queryset = queryset.filter(review_status=status)
    if change_type:
        queryset = queryset.filter(change_type=change_type)
    if scope:
        queryset = queryset.filter(affected_scope=scope)
    if query:
        queryset = queryset.filter(
            Q(affected_norm__number__icontains=query)
            | Q(affected_norm__canonical_title__icontains=query)
            | Q(amending_norm__number__icontains=query)
            | Q(evidence_text__icontains=query)
        )
    if min_confidence:
        try:
            queryset = queryset.filter(confidence__gte=Decimal(min_confidence))
        except InvalidOperation:
            pass
    return queryset


def home(request):
    return render(request, "review/home.html")


@login_required
def dashboard(request):
    return redirect("review:management" if request.user.is_staff else "review:queue")


@login_required
def change_list(request):
    queryset = filtered_changes(request)
    paginator = Paginator(queryset, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "review/change_list.html", {"page": page})


@login_required
def change_detail(request, pk):
    change = get_object_or_404(
        LegalChange.objects.select_related(
            "affected_norm", "amending_norm", "affected_article", "source_article_version",
        ),
        pk=pk,
    )
    form = ReviewForm(instance=change)
    amending_article, amending_article_version = resolve_amending_article(change)
    next_change = LegalChange.objects.filter(review_status="pendiente", id__gt=change.id).order_by("id").first()
    if next_change is None:
        next_change = LegalChange.objects.filter(review_status="pendiente").exclude(id=change.id).order_by("id").first()
    return render(request, "review/change_detail.html", {
        "change": change,
        "form": form,
        "next_change": next_change,
        "amending_article": amending_article,
        "amending_article_version": amending_article_version,
    })


@validator_required
def review_change(request, pk):
    if request.method != "POST":
        raise Http404
    change = get_object_or_404(LegalChange, pk=pk)
    task = change.reviewtask_set.order_by("-id").first()
    if task:
        return redirect("review:task", pk=task.pk)
    form = ReviewForm(request.POST, instance=change)
    if form.is_valid():
        reviewed = form.save(commit=False)
        if reviewed.review_status == "pendiente":
            reviewed.reviewed_at = None
            reviewed.reviewed_by = None
        else:
            reviewed.reviewed_at = timezone.now()
            reviewed.reviewed_by = request.user.get_username()
        reviewed.updated_at = timezone.now()
        reviewed.save(update_fields=["review_status", "review_notes", "reviewed_at", "reviewed_by", "updated_at"])
        messages.success(request, f"Cambio #{change.id} actualizado como {reviewed.review_status}.")
        next_id = request.POST.get("next_id")
        if next_id:
            return redirect("review:detail", pk=next_id)
        return redirect("review:detail", pk=change.id)
    amending_article, amending_article_version = resolve_amending_article(change)
    return render(request, "review/change_detail.html", {
        "change": change,
        "form": form,
        "next_change": None,
        "amending_article": amending_article,
        "amending_article_version": amending_article_version,
    }, status=400)
