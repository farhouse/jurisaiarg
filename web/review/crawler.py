import uuid

from django import forms
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_GET

from .models import CrawlJob, Norm
from .workflow import staff_required


class CrawlForm(forms.Form):
    start_id = forms.IntegerField(label="Desde el índice", min_value=1, max_value=2147483647)
    end_id = forms.IntegerField(label="Hasta el índice", min_value=1, max_value=2147483647)
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        start, end = data.get("start_id"), data.get("end_id")
        if start is not None and end is not None:
            if end < start:
                self.add_error("end_id", "El índice final debe ser igual o mayor que el inicial.")
            elif end - start >= 1000:
                self.add_error("end_id", "Podés descargar hasta 1.000 índices por lote. Dividí el rango en varios lotes.")
        return data


@staff_required
@require_http_methods(["GET", "POST"])
def jobs(request):
    form = CrawlForm(request.POST if request.method == "POST" else None,
                     initial={"submission_id": uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        # UUID único: doble clic o reenvío del mismo formulario no duplican el trabajo.
        job, _ = CrawlJob.objects.get_or_create(submission_id=data["submission_id"], defaults={
            "start_id": data["start_id"], "end_id": data["end_id"],
            "requested_by": request.user.get_username(), "created_at": timezone.now(),
        })
        return redirect("review:crawl_job", pk=job.pk)
    last_id = Norm.objects.exclude(infoleg_id=None).order_by("-infoleg_id").values_list("infoleg_id", flat=True).first()
    return render(request, "review/crawl_jobs.html", {
        "form": form, "last_id": last_id,
        "page": Paginator(CrawlJob.objects.select_related("crawl_run"), 20).get_page(request.GET.get("page")),
    }, status=400 if request.method == "POST" else 200)


@staff_required
@require_GET
def job_detail(request, pk):
    job = get_object_or_404(CrawlJob.objects.select_related("crawl_run"), pk=pk)
    errors = job.crawl_run.items.filter(status__in=["error_descarga", "error_parseo"]) if job.crawl_run else []
    return render(request, "review/crawl_job.html", {
        "job": job, "errors": errors, "processed": job.crawl_run.processed_count if job.crawl_run else 0,
    })
