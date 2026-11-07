from django.contrib import admin

from .models import LegalChange


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
    actions = None

    # Las decisiones pasan por el validador para conservar auditoría y conflictos.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
