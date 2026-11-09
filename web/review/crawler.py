import uuid

from django import forms
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_GET

from .models import CrawlJob, CrawlRun, Norm
from .workflow import staff_required


class CrawlForm(forms.Form):
    mode = forms.ChoiceField(label='Qué querés hacer', choices=[c for c in CrawlJob.MODES if c[0] != 'pilot'], initial='full')
    run = forms.ModelChoiceField(label='Lote descargado', queryset=CrawlRun.objects.none(), required=False)
    start_id = forms.IntegerField(label="Desde el índice", min_value=1, max_value=2147483647, required=False)
    end_id = forms.IntegerField(label="Hasta el índice", min_value=1, max_value=2147483647, required=False)
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['run'].queryset = CrawlRun.objects.filter(finished_at__isnull=False, status__in=["ok", "partial"]).exclude(requested_ids=[]).exclude(requested_ids=None)
        self.fields['run'].label_from_instance = lambda run: run.label
        if self.is_bound:
            # Los campos de otra tarea no participan en la validación ni el envío.
            inactive = ('start_id', 'end_id') if self.data.get('mode') == 'process' else ('run',)
            for name in inactive:
                self.fields[name].disabled = True

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'process':
            run = data.get('run')
            if not run or not run.requested_ids:
                self.add_error('run', 'Elegí un lote cuya descarga haya terminado.')
            elif max(run.requested_ids) - min(run.requested_ids) >= 1000:
                self.add_error('run', 'Este lote supera el límite de 1.000 índices.')
            else:
                data['start_id'], data['end_id'] = min(run.requested_ids), max(run.requested_ids)
            return data
        for field in ('start_id', 'end_id'):
            if data.get(field) is None:
                self.add_error(field, 'Indicá el rango que querés descargar.')
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
            "mode": data['mode'], "crawl_run": data['run'] if data['mode'] == 'process' else None,
            "requested_by": request.user.get_username(), "created_at": timezone.now(),
        })
        return redirect("review:crawl_job", pk=job.pk)
    last_id = Norm.objects.exclude(infoleg_id=None).order_by("-infoleg_id").values_list("infoleg_id", flat=True).first()
    return render(request, "review/crawl_jobs.html", {
        "form": form, "last_id": last_id,
        "page": Paginator(CrawlJob.objects.exclude(mode='pilot').select_related("crawl_run"), 20).get_page(request.GET.get("page")),
    }, status=400 if request.method == "POST" else 200)


def duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours} h {minutes} min' if hours else (f'{minutes} min {seconds} s' if minutes else f'{seconds} s')


@staff_required
@require_GET
def job_detail(request, pk):
    job = get_object_or_404(CrawlJob.objects.select_related("crawl_run"), pk=pk)
    if job.mode == 'pilot':
        return redirect('review:pilot_detail', pk=job.pilot.pk)
    errors = job.crawl_run.items.filter(status__in=["error_descarga", "error_parseo"]) if job.crawl_run else []
    now = timezone.now()
    end = job.finished_at or now
    heartbeat_age = (now - job.heartbeat_at).total_seconds() if job.heartbeat_at else None
    activity_age = (end - job.last_activity_at).total_seconds() if job.last_activity_at else None
    return render(request, "review/crawl_job.html", {
        "job": job, "errors": errors, "processed": job.crawl_run.processed_count if job.crawl_run else 0,
        "elapsed": duration((end - job.started_at).total_seconds()) if job.started_at else None,
        "heartbeat_age": duration(heartbeat_age) if heartbeat_age is not None else None,
        "activity_age": duration(activity_age) if activity_age is not None else None,
        "signal_stale": heartbeat_age is not None and heartbeat_age >= 60,
        "progress_stale": activity_age is not None and activity_age >= 300,

    })
