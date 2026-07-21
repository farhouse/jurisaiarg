from django.contrib import admin
from django.utils import timezone

from .models import LegalChange


@admin.action(description="Marcar como validadas")
def validate_changes(modeladmin, request, queryset):
    queryset.update(review_status="validada", reviewed_at=timezone.now(), reviewed_by=request.user.get_username())


@admin.action(description="Marcar como rechazadas")
def reject_changes(modeladmin, request, queryset):
    queryset.update(review_status="rechazada", reviewed_at=timezone.now(), reviewed_by=request.user.get_username())


@admin.register(LegalChange)
class LegalChangeAdmin(admin.ModelAdmin):
    list_display = (
        "id", "affected_norm", "affected_article", "change_type",
        "amending_norm", "confidence", "review_status", "reviewed_by",
    )
    list_filter = ("review_status", "change_type", "affected_scope", "parser_version")
    search_fields = (
        "affected_norm__number", "affected_norm__canonical_title",
        "amending_norm__number", "evidence_text",
    )
    readonly_fields = (
        "affected_norm", "affected_article", "amending_norm",
        "amending_reference_raw", "amending_article_reference", "change_type",
        "affected_scope", "publication_date", "effect_date", "evidence_text",
        "parser_version", "confidence", "fingerprint", "created_at", "updated_at",
        "reviewed_at", "reviewed_by",
    )
    actions = (validate_changes, reject_changes)

    def save_model(self, request, obj, form, change):
        obj.reviewed_at = timezone.now()
        obj.reviewed_by = request.user.get_username()
        super().save_model(request, obj, form, change)
