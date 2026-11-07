from django import forms
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.debug import sensitive_post_parameters

from .models import NormReview, ReviewEvent
from .workflow import staff_required


class ValidatorCreateForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        fields = ("username", "first_name", "last_name", "email")


class ValidatorEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "is_active")
        labels = {"is_active": "Permitir acceso"}
        help_texts = {"is_active": "Al desactivar la cuenta, la persona no podrá ingresar ni seguir validando. Sus revisiones se conservan."}


def validator_accounts():
    # Este panel no modifica administradores ni concede privilegios adicionales.
    return User.objects.filter(groups__name="Validadores", is_staff=False, is_superuser=False)


@staff_required
@require_http_methods(["GET"])
def index(request):
    accounts = validator_accounts()
    query = request.GET.get("q", "").strip()
    state = request.GET.get("state", "")
    if query:
        accounts = accounts.filter(Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query))
    if state in ("active", "inactive"):
        accounts = accounts.filter(is_active=state == "active")
    page = Paginator(accounts.order_by("username"), 25).get_page(request.GET.get("page"))
    counts = {row["user_id"]: row for row in NormReview.objects.filter(user_id__in=[u.pk for u in page], task__kind="indice").values("user_id").annotate(
        approved=Count("pk", filter=Q(status="validada")),
        rejected=Count("pk", filter=Q(status="rechazada")),
        pending=Count("pk", filter=Q(status="pendiente")),
    )}
    for account in page:
        account.review_counts = counts.get(account.pk, {"approved": 0, "rejected": 0, "pending": 0})
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "review/validators.html", {"page": page, "query": query, "state": state, "params": params.urlencode()})


@sensitive_post_parameters("password1", "password2")
@staff_required
@require_http_methods(["GET", "POST"])
def create(request):
    form = ValidatorCreateForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            account = form.save()
            group, _ = Group.objects.get_or_create(name="Validadores")
            account.groups.add(group)
        messages.success(request, f"Cuenta {account.username} creada. Compartí las credenciales por un canal privado; no se envió un correo automático.")
        return redirect("review:validator_detail", pk=account.pk)
    return render(request, "review/validator_form.html", {"form": form}, status=400 if request.method == "POST" else 200)


@staff_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    account = get_object_or_404(validator_accounts(), pk=pk)
    form = ValidatorEditForm(request.POST if request.method == "POST" else None, instance=account)
    if request.method == "POST" and form.is_valid():
        # Actualizar solo campos permitidos evita sobrescribir contraseña o roles.
        User.objects.filter(pk=account.pk, is_staff=False, is_superuser=False, groups__name="Validadores").update(**form.cleaned_data)
        messages.success(request, "Datos y acceso actualizados. Las revisiones se conservaron.")
        return redirect("review:validator_detail", pk=account.pk)
    votes = NormReview.objects.filter(user_id=account.pk, task__kind="indice").select_related("task__norm", "task__crawl_run").order_by("-updated_at", "-pk")
    page = Paginator(votes, 20).get_page(request.GET.get("page"))
    # Los pasajes guardan eventos por nombre de usuario; aquí no se permite renombrarlo.
    events = ReviewEvent.objects.filter(username=account.username, task__kind="pasaje").select_related("task__norm").order_by("-created_at", "-pk")[:20]
    return render(request, "review/validator_detail.html", {"account": account, "form": form, "page": page, "events": events}, status=400 if request.method == "POST" else 200)
