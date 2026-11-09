from django import forms

from .models import LegalChange


def decision_errors(form, url, context=''):
    """Use Django's actual validation errors for the summary, API and field links."""
    labels = {'status':'Decisión', 'issues':'Problemas encontrados', 'check':'Control de la copia',
              'revision':'Versión de la revisión', 'proposal_kind':'Propuesta',
              'keep_matters':'Materias jurídicas', 'keep_concepts':'Conceptos para la búsqueda',
              'amending_reference':'Norma de origen', 'amending_article':'Artículo de origen',
              'affected_reference':'Norma referida', 'affected_article':'Artículo referido',
              'change_type':'Tipo de relación', 'scope':'Alcance', 'scope_detail':'Inciso o párrafo exacto'}
    errors=[]
    for name, messages in form.errors.items():
        field=form[name] if name in form.fields else None
        target='case-decision'
        if field and not field.is_hidden:
            target=field.auto_id
            if isinstance(field.field.widget,(forms.RadioSelect,forms.CheckboxSelectMultiple)):
                target=next((widget.id_for_label for widget in field),target)
        label=labels.get(name,field.label if field else 'Revisión')
        for message in messages:
            errors.append({'field':name,'label':label,'message':str(message),'context':context,
                           'url':url+'&validate=1#'+target})
    return errors


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


class DecisionForm(forms.Form):
    status = forms.ChoiceField(choices=[("validada", "Correcto"), ("rechazada", "Incorrecto"), ("pendiente", "Dejar pendiente")], widget=forms.RadioSelect)
    notes = forms.CharField(required=False, max_length=5000, label="Observaciones", widget=forms.Textarea(attrs={"rows": 4}))
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def has_correction(self, data):
        return False

    def clean(self):
        data = super().clean()
        if data.get("status") == "rechazada" and not data.get("notes", "").strip() and not data.get('issues') and not self.has_correction(data):
            self.add_error("notes", "Indicá qué está mal y qué habría que corregir.")
        return data
