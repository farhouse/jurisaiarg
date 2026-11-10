"""Correcciones tipadas, con evidencia visible; el administrador no edita JSON."""
from copy import deepcopy
from django import forms
from .pilot_contract import MATTERS, empty_result, validate_result

DECISIONS = [('pending', 'Sin decisión'), ('confirmed', 'Confirmar sin cambios'),
             ('corrected', 'Corregir propuesta'), ('legal', 'Requiere criterio jurídico')]
RELATIONS = [('cita','Cita o remisión'),('sustituye','Sustituye'),('incorpora','Incorpora'),
             ('deroga','Deroga'),('modifica','Modifica'),('ambiguo','Efecto por determinar')]
SCOPES = [(v,v.capitalize()) for v in ('norma','articulo','inciso','parrafo','anexo','expresion','desconocido')]


class BlockForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    elapsed_ms = forms.IntegerField(widget=forms.HiddenInput, min_value=0,max_value=3600000,initial=0,required=False)
    decision = forms.ChoiceField(label='Tu decisión', choices=DECISIONS, widget=forms.RadioSelect)
    notes = forms.CharField(label='Observaciones', required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self, block, value, snapshot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.block, self.value, self.snapshot = block, value, snapshot
        units = [(u['id'], ('Artículo ' if u['kind']=='article' else '') + u['label']) for u in snapshot['units']]
        defaults = {}
        if block == 'summary':
            self.fields['text'] = forms.CharField(label='Resumen corregido', required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':5}))
            defaults['text'] = value['text']
        elif block == 'matters':
            self.fields['primary'] = forms.ChoiceField(label='Materia principal', required=False, choices=[('','Sin determinar')]+[(m,m) for m in MATTERS])
            self.fields['secondary'] = forms.MultipleChoiceField(label='Materias secundarias', required=False, choices=[(m,m) for m in MATTERS], widget=forms.CheckboxSelectMultiple)
            defaults.update(primary=next((m['label'] for m in value if m['primary']), ''), secondary=[m['label'] for m in value if not m['primary']])
        elif block == 'concepts':
            for field, role, label in [('central','central','Conceptos centrales, uno por línea'),('mentions','mencion','Menciones útiles, una por línea')]:
                self.fields[field] = forms.CharField(label=label, required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':4}))
                defaults[field] = '\n'.join(c['label'] for c in value if c['role']==role)
        elif block == 'articles':
            for entry in value:
                unit = next(u for u in snapshot['units'] if u['id']==entry['unit_id'])
                name = 'function_' + entry['unit_id']
                self.fields[name] = forms.CharField(label='Función · '+unit['label'], required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':2}))
                defaults[name] = entry['function']
        elif block == 'relations':
            # Una fila vacía permite agregar una relación omitida sin introducir un editor de JSON.
            self.relation_count = len(value) + 1
            if self.is_bound:
                try:
                    self.relation_count = max(1,min(200,int(self.data.get(self.add_prefix('relation_count'),1))))
                except (ValueError,TypeError):
                    self.relation_count = 1
            self.fields['relation_count'] = forms.IntegerField(widget=forms.HiddenInput, min_value=1,max_value=200)
            defaults['relation_count'] = self.relation_count
            for i in range(self.relation_count):
                entry = value[i] if i<len(value) else {}
                fields = {'source_unit_id': forms.ChoiceField(label='Fragmento de origen', choices=units, required=False),
                    'direction': forms.ChoiceField(label='Dirección', choices=[('outgoing','Esta norma cita o modifica a la referida'),('incoming','La referida modifica esta norma')], required=False),
                    'type': forms.ChoiceField(label='Relación', choices=RELATIONS, required=False),
                    'target_type': forms.CharField(label='Tipo de norma referida', required=False, max_length=100),
                    'target_number': forms.CharField(label='Número de norma referida', required=False, max_length=100),
                    'target_article': forms.CharField(label='Artículo referido', required=False, max_length=100),
                    'scope': forms.ChoiceField(label='Alcance', choices=SCOPES, required=False),
                    'detail': forms.CharField(label='Detalle del alcance', required=False, max_length=5000),
                    'quote': forms.CharField(label='Cita literal de respaldo', required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':3})),
                    'remove': forms.BooleanField(label='Quitar esta relación', required=False)}
                for name, field in fields.items():
                    key = f'rel_{i}_{name}'
                    self.fields[key] = field
                    defaults[key] = (entry.get('evidence') or [{}])[0].get('quote','') if name=='quote' else entry.get(name, '')
        if block in ('summary','matters','concepts'):
            self.fields['evidence_unit'] = forms.ChoiceField(label='Fragmento que respalda la corrección', required=False, choices=[('','Elegir fragmento')]+units)
            self.fields['quote'] = forms.CharField(label='Cita literal para los datos nuevos o cambiados', required=False, max_length=5000,
                widget=forms.Textarea(attrs={'rows':3,'placeholder':'Copiá el pasaje exacto de la fuente que ves a la izquierda.'}))
        self.initial = {**defaults, **self.initial}

    @property
    def relation_rows(self):
        return [{'new':i>=len(self.value), 'fields':[self[name] for name in self.fields if name.startswith(f'rel_{i}_')]}
                for i in range(self.relation_count)]

    def build_value(self):
        data = self.cleaned_data
        if data['decision'] not in ('corrected','legal'):
            return deepcopy(self.value)
        evidence = [{'unit_id': data.get('evidence_unit',''), 'quote': data.get('quote','')}]
        if self.block == 'summary':
            return {'text': data['text'].strip(), 'evidence': self.value['evidence'] if data['text'].strip()==self.value['text'] else evidence}
        if self.block == 'matters':
            labels = ([data['primary']] if data['primary'] else []) + [m for m in data['secondary'] if m != data['primary']]
            return [{'label': m, 'primary': m==data['primary'], 'evidence': next((v['evidence'] for v in self.value if v['label']==m), evidence)} for m in labels]
        if self.block == 'concepts':
            entries = [(label.strip(), role) for field,role in [('central','central'),('mentions','mencion')]
                       for label in data[field].splitlines() if label.strip()]
            return [{'label': label, 'role': role, 'evidence': next((v['evidence'] for v in self.value if v['label']==label), evidence)} for label,role in entries]
        if self.block == 'articles':
            return [{**v,'function':data['function_'+v['unit_id']].strip()} for v in self.value]
        if self.block == 'relations':
            values = []
            for i in range(self.relation_count):
                entry = {k.removeprefix(f'rel_{i}_'):v for k,v in data.items() if k.startswith(f'rel_{i}_')}
                if entry.pop('remove') or not entry.get('target_number'):
                    continue
                literal = entry.pop('quote')
                entry['evidence'] = [{'unit_id': entry['source_unit_id'], 'quote': literal}]
                values.append(entry)
            return values
        return deepcopy(self.value)

    def clean(self):
        data = super().clean()
        if self.errors or not data.get('decision'):
            return data
        if data['decision'] == 'legal' and not data.get('notes','').strip():
            self.add_error('notes','Indicá qué duda necesita criterio jurídico.')
            return data
        value = self.build_value()
        if data['decision'] in ('confirmed','corrected'):
            result = empty_result()
            result[self.block] = value
            try:
                validate_result(result, self.snapshot, complete=False)
                if self.block=='summary' and not value['text'].strip():
                    raise ValueError('Escribí un resumen o señalá qué duda impide prepararlo.')
                if self.block=='articles' and any(not v['function'].strip() for v in value):
                    raise ValueError('Falta describir la función de un artículo. Corregí el bloque o indicá la duda jurídica.')
            except ValueError as exc:
                self.add_error(None, str(exc))
        data['value'] = value
        return data
