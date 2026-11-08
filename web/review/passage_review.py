"""Propuestas humanas separadas del resultado del parser y del clasificador."""
import re
from urllib.parse import urlencode

from django import forms
from django.http import Http404
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from . import copy_review
from .forms import DecisionForm


CHANGES = [('sustituye', 'Sustitución'), ('incorpora', 'Incorporación'), ('deroga', 'Derogación'),
           ('modifica', 'Modificación'), ('ninguno', 'Sin cambio detectado'), ('ambiguo', 'Sin determinar')]
SCOPES = [('articulo', 'Artículo completo'), ('inciso', 'Inciso'), ('parrafo', 'Párrafo'),
          ('anexo', 'Anexo'), ('norma', 'Norma completa'), ('indeterminado', 'Sin determinar'),
          ('no_aplica', 'Sin relación detectada')]
RELATION_FIELDS = [('amending_reference', 'Norma modificadora'), ('amending_article', 'Artículo modificador'),
                   ('affected_reference', 'Norma afectada'), ('affected_article', 'Artículo afectado'),
                   ('change_type', 'Tipo de cambio'), ('scope', 'Alcance'), ('scope_detail', 'Inciso o párrafo exacto')]


def original_proposal(task):
    if task.model_result.get('category') == 'temas':
        return {'kind': 'topics', 'matters': [m['label'] for m in task.model_result.get('matters', [])],
                'concepts': list(task.model_result.get('concepts', []))}
    change = task.legal_change
    if change:
        return {'kind': 'relations', 'amending_reference': change.amending_norm.display_reference if change.amending_norm_id else change.amending_reference_raw,
                'amending_article': change.amending_article_reference or '',
                'affected_reference': change.affected_norm.display_reference,
                'affected_article': change.affected_article.article_label,
                'change_type': change.change_type, 'scope': change.affected_scope, 'scope_detail': ''}
    prediction = task.prediction or task.model_result.get('prediction') or 'ambiguo'
    return {'kind': 'relations', 'amending_reference': '', 'amending_article': '',
            'affected_reference': '' if prediction == 'ninguno' else task.norm.display_reference,
            'affected_article': task.article_version.article.article_label if task.article_version_id and prediction != 'ninguno' else '',
            'change_type': prediction, 'scope': 'no_aplica' if prediction == 'ninguno' else 'indeterminado', 'scope_detail': ''}


def changes_between(original, proposed):
    changes = []
    if original['kind'] == 'topics':
        for key, name in [('matters', 'materia'), ('concepts', 'concepto')]:
            before, after = original[key], proposed[key]
            changes.extend(f'Quitar {name}: {label}' for label in before if label not in after)
            changes.extend(f'Agregar {name}: {label}' for label in after if label not in before)
    else:
        labels = {**dict(CHANGES), **dict(SCOPES)}
        for key, label in RELATION_FIELDS:
            if original.get(key, '') != proposed.get(key, ''):
                before, after = original.get(key, ''), proposed.get(key, '')
                changes.append(f"{label}: {labels.get(before, before) or 'Sin dato'} → {labels.get(after, after) or 'Sin dato'}")
    return changes


class TagsField(forms.CharField):
    widget = forms.Textarea(attrs={'rows': 2})

    def clean(self, value):
        text = super().clean(value)
        labels, seen = [], set()
        for item in re.split(r'[,;\n]', text):
            label = ' '.join(item.split())
            if not label:
                continue
            if len(label) > 80:
                raise forms.ValidationError('Cada etiqueta debe tener hasta 80 caracteres.')
            if label.casefold() not in seen:
                labels.append(label)
                seen.add(label.casefold())
        if len(labels) > 20:
            raise forms.ValidationError('Proponé hasta 20 etiquetas por campo.')
        return labels


class ProposalForm(DecisionForm):
    proposal_kind = forms.ChoiceField(widget=forms.HiddenInput)
    issues = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, task, **kwargs):
        self.original = original_proposal(task)
        self.proposal_kind = self.original['kind']
        self.correction, self.changes = {}, []
        super().__init__(*args, **kwargs)
        self.fields['proposal_kind'].choices = [(self.proposal_kind, self.proposal_kind)]
        self.fields['status'].choices = [('validada', 'Propuesta correcta' if self.proposal_kind == 'topics' else 'Relación correcta'),
                                        ('rechazada', 'Necesita corrección'), ('pendiente', 'No puedo comprobarlo')]

    def has_correction(self, data):
        return bool(changes_between(self.original, self.build_proposal(data)))

    def clean(self):
        data = super().clean()
        self.correction = self.build_proposal(data)
        self.changes = changes_between(self.original, self.correction)
        if data.get('status') == 'validada' and (self.changes or data.get('issues')):
            self.add_error('status', 'Propusiste cambios: elegí «Necesita corrección» o deshacelos para validar la propuesta original.')
        return data


class TopicsForm(ProposalForm):
    keep_matters = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    add_matters = TagsField(required=False, max_length=2000)
    keep_concepts = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    add_concepts = TagsField(required=False, max_length=2000)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['issues'].choices = [('missing', 'Faltan etiquetas'), ('irrelevant', 'Etiquetas irrelevantes'), ('context', 'Falta contexto')]
        for name, key in [('keep_matters', 'matters'), ('keep_concepts', 'concepts')]:
            self.fields[name].choices = [(label, label) for label in self.original[key]]

    def build_proposal(self, data):
        result = {'kind': 'topics'}
        for key in ('matters', 'concepts'):
            labels = list(data.get('keep_' + key, []))
            seen = {label.casefold() for label in labels}
            for label in data.get('add_' + key, []):
                if label.casefold() not in seen:
                    labels.append(label)
                    seen.add(label.casefold())
            result[key] = labels
        return result


class RelationsForm(ProposalForm):
    amending_reference = forms.CharField(required=False, max_length=160)
    amending_article = forms.CharField(required=False, max_length=80)
    affected_reference = forms.CharField(required=False, max_length=160)
    affected_article = forms.CharField(required=False, max_length=80)
    change_type = forms.ChoiceField(choices=CHANGES)
    scope = forms.ChoiceField(choices=SCOPES)
    scope_detail = forms.CharField(required=False, max_length=160, widget=forms.TextInput(attrs={'placeholder': 'Por ejemplo: inciso b) o segundo párrafo'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['issues'].choices = [('scope', 'Alcance incorrecto'), ('reference', 'Referencia incorrecta'),
            ('type', 'Tipo de cambio incorrecto'), ('citation', 'Solo es una cita'), ('missing', 'Falta una relación'), ('context', 'Falta contexto')]
        for key in ('change_type', 'scope'):
            value = self.original[key]
            if value not in dict(self.fields[key].choices):
                self.fields[key].choices = [*self.fields[key].choices, (value, value or 'Sin dato')]

    def build_proposal(self, data):
        return {'kind': 'relations', **{key: data.get(key, self.original.get(key, '')) for key, _ in RELATION_FIELDS}}

    def clean(self):
        data = super().clean()
        if data.get('status') == 'validada' and data.get('change_type') != 'ninguno':
            for key in ('amending_reference', 'affected_reference'):
                if not data.get(key):
                    self.add_error(key, 'Falta identificar esta norma. Dejá pendiente o proponé la corrección.')
            if data.get('scope') in ('inciso', 'parrafo') and not data.get('scope_detail'):
                self.add_error('scope_detail', 'Falta identificar el inciso o párrafo exacto.')
            if data.get('scope') in ('articulo', 'inciso', 'parrafo') and not data.get('affected_article'):
                self.add_error('affected_article', 'Falta identificar el artículo afectado.')
            if data.get('change_type') == 'ambiguo' or data.get('scope') == 'indeterminado':
                self.add_error('status', 'La relación todavía no está determinada. Dejá pendiente o proponé la corrección.')
        return data


def proposal_form(task, data, initial):
    original = original_proposal(task)
    proposed = task.proposed_correction or original
    initial = {**initial, 'proposal_kind': original['kind'], 'issues': proposed.get('issues', [])}
    if original['kind'] == 'topics':
        for key in ('matters', 'concepts'):
            initial['keep_' + key] = [label for label in original[key] if label in proposed[key]]
            initial['add_' + key] = '\n'.join(label for label in proposed[key] if label not in original[key])
        form_class = TopicsForm
    else:
        initial.update({key: proposed.get(key, original[key]) for key, _ in RELATION_FIELDS})
        form_class = RelationsForm
    return form_class(data, task=task, initial=initial)


def highlighted(text, terms):
    terms = sorted({term for term in terms if term}, key=len, reverse=True)
    if not terms:
        return format_html('{}', text)
    pattern = re.compile('(' + '|'.join(re.escape(term) for term in terms) + ')', re.I)
    return format_html_join('', '{}', ((format_html('<mark>{}</mark>', part) if i % 2 else format_html('{}', part),)
                                      for i, part in enumerate(pattern.split(text))))


def passage_context(task, request, current_url):
    original = original_proposal(task)
    topics = original['kind'] == 'topics'
    labels = [('evidence', 'Texto' if topics else 'Evidencia'), ('matters', 'Materias'), ('concepts', 'Conceptos'), ('summary', 'Resumen')] if topics else [
        ('evidence', 'Evidencia'), ('references', 'Referencias'), ('scope', 'Alcance'), ('summary', 'Resumen')]
    selected_step = request.GET.get('section', 'matters' if topics else 'scope')
    if selected_step not in dict(labels):
        raise Http404
    steps = [{'key': key, 'label': label, 'url': '#passage-' + key,
              'active': key == selected_step, 'complete': task.review_status != 'pendiente'} for key, label in labels]
    version = task.article_version.norm_version.version_type if task.article_version_id else None
    sources = list(copy_review.available_sources(task).defer('raw_content').order_by('id'))
    primary = [s for s in sources if s.norm_id == task.norm_id]
    kind = 'texto_original' if version == 'original' else 'texto_actualizado' if version else 'ficha'
    source = next((s for s in reversed(primary) if s.document_kind == kind), None)
    if not source and not version:
        source = next(iter(reversed(primary)), None)
    viewers = []
    if source:
        source_label = ('Norma afectada' if task.norm_id == task.legal_change.affected_norm_id else 'Norma modificadora') if not topics and task.legal_change_id else 'Fuente de InfoLEG'
        focus = task.article_version.article.article_label if task.article_version_id else ''
        viewers.append({'key': 'official', 'label': source_label, 'source': source,
            'reference': task.norm.display_reference, 'article': focus,
            'version_label': 'Ficha oficial' if source.document_kind == 'ficha' else 'Texto original' if source.document_kind == 'texto_original' else 'Texto actualizado',
            'url': reverse('review:copy_source', args=[task.pk, source.pk]) + '?' + urlencode({'focus': focus}) + '#jurisai-focus',
            'official_url': copy_review.safe_official_url(source.url), 'same_batch': True})
    if not topics and task.legal_change_id:
        related_id = task.legal_change.amending_norm_id if task.legal_change.amending_norm_id != task.norm_id else task.legal_change.affected_norm_id
        related = [s for s in sources if s.norm_id == related_id and s.norm_id != task.norm_id]
        counterpart = next((s for s in reversed(related) if s.document_kind == 'texto_original'), next(iter(reversed(related)), None))
        if counterpart:
            focus = (task.legal_change.amending_article_reference or '') if counterpart.norm_id == task.legal_change.amending_norm_id else task.legal_change.affected_article.article_label
            viewers.append({'key': 'counterpart', 'label': 'Norma modificadora' if counterpart.norm_id == task.legal_change.amending_norm_id else 'Norma afectada', 'source': counterpart,
                'reference': task.legal_change.amending_norm.display_reference if counterpart.norm_id == task.legal_change.amending_norm_id else task.legal_change.affected_norm.display_reference,
                'article': focus, 'version_label': 'Ficha oficial' if counterpart.document_kind == 'ficha' else 'Texto original' if counterpart.document_kind == 'texto_original' else 'Texto actualizado',
                'url': reverse('review:copy_source', args=[task.pk, counterpart.pk]) + '?' + urlencode({'focus': focus}) + '#jurisai-focus',
                'official_url': copy_review.safe_official_url(counterpart.url), 'same_batch': counterpart.crawl_run_id == task.crawl_run_id})
    terms = original['concepts'] if topics else [original['amending_reference'], original['affected_reference'], original['scope_detail']]
    return {'topics': topics, 'original': original, 'steps': steps, 'viewers': viewers,
            'has_counterpart': any(v['key'] == 'counterpart' for v in viewers),
            'default_view': 'official' if source else 'fragment',
            'fragment_html': highlighted(task.source_text, terms),
            'version_label': copy_review.VERSIONS.get(version, 'Ficha o fragmento'),
            'changes': changes_between(original, task.proposed_correction) if task.proposed_correction else [],
            'relation_fields': [{'key': key, 'label': label, 'value': dict(CHANGES + SCOPES).get(original[key], original[key]) or 'Sin dato'} for key, label in RELATION_FIELDS] if not topics else []}
