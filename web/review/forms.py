from django import forms

from .models import LegalChange


class ReviewForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["review_status"].choices = LegalChange.REVIEW_CHOICES
        self.fields["review_notes"].required = False

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("review_status")
        notes = (cleaned_data.get("review_notes") or "").strip()
        if status == "rechazada" and not notes:
            self.add_error(
                "review_notes",
                "Indicá por qué se rechaza o qué debería corregirse.",
            )
        cleaned_data["review_notes"] = notes or None
        return cleaned_data

    class Meta:
        model = LegalChange
        fields = ["review_status", "review_notes"]
        labels = {
            "review_status": "Estado",
            "review_notes": "Notas de revisión",
        }
        widgets = {
            "review_status": forms.RadioSelect,
            "review_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Aclaración opcional o motivo por el que la relación es incorrecta…"}),
        }
