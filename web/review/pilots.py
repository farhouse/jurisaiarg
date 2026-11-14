"""Administración y evaluaciones del piloto, independientes de la revisión habitual."""
from copy import deepcopy
import json
import os
import uuid
from urllib.error import URLError
from urllib.request import urlopen

from django import forms
from django.core.cache import cache
from django.db import transaction
from django.db.models import Value,TextField
from django.db.models.functions import Replace
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_GET

from .models import AnalysisPilot, AnalysisPilotItem, AnalysisPilotEvaluation, CrawlJob, CrawlRun, Norm, ReviewCard
from .pilot_contract import (BLOCKS, DEFAULT_MODEL, VERSION, PROMPT_VERSION, analysis_resources,
    request_budget, DEFAULT_MAX_TOKENS, DEFAULT_REASONING_TOKENS, STAGE_CHOICES, processing_stages, norm_groups)
from .pilot_forms import BlockForm
from .workflow import staff_required


def model_choices():
    """Catálogo público; la clave sigue exclusivamente en el crawler."""
    key = 'jurisai.pilot-models.v1'
    saved = cache.get(key)
    if saved is not None:
        return saved
    try:
        with urlopen('https://openrouter.ai/api/v1/models',timeout=5) as response:
            raw = response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:
            raise ValueError('Catálogo demasiado grande')
        models = json.loads(raw)['data']
        choices = {}
        for model in models:
            identifier = model.get('id','')
            if (isinstance(identifier,str) and identifier.endswith(':free') and len(identifier)<=200
                    and {'structured_outputs','response_format'} <= set(model.get('supported_parameters',[]))
                    and all(float(model.get('pricing',{}).get(k,'1'))==0 for k in ('prompt','completion'))):
                choices[identifier] = model.get('name') or identifier
        result = (sorted(choices.items(),key=lambda row:row[1].casefold()),'')
        if not choices:
            result = ([], 'OpenRouter no anuncia modelos gratuitos compatibles en este momento. Volvé a intentar más tarde.')
        cache.set(key,result,300)
        return result
    except (URLError,OSError,ValueError,KeyError,TypeError,AttributeError):
        default = os.environ.get('JURISAI_PILOT_MODEL',DEFAULT_MODEL)
        choices = [(default,default+' · comprobar al ejecutar')] if default.endswith(':free') else []
        result = (choices,'No se pudo consultar el catálogo de OpenRouter. Por ahora solo se ofrece el modelo configurado; se comprobará antes de ejecutar.')
        cache.set(key,result,30)
        return result


class ModelForm(forms.Form):
    stages = forms.MultipleChoiceField(label='Etapas a ejecutar',choices=STAGE_CHOICES,initial=['laya','llm'],
        widget=forms.CheckboxSelectMultiple,error_messages={'required':'Seleccioná al menos una etapa.'},
        help_text='La muestra usa las fuentes guardadas. No vuelve a descargar ni genera embeddings.')
    model = forms.ChoiceField(label='Modelo de OpenRouter',choices=[],
        help_text='Modelos gratuitos que anuncian salida estructurada. El trabajador comprueba la disponibilidad antes de analizar.')
    max_tokens = forms.IntegerField(label='Límite total de salida (tokens)',min_value=2048,max_value=131072,
        help_text='Incluye el razonamiento y la ficha JSON. No es el tamaño de la ley.')
    reasoning_tokens = forms.IntegerField(label='Límite de razonamiento (tokens)',min_value=1024,max_value=130048,
        help_text='Forma parte del límite total. Solo se envía si el modelo permite limitarlo.')
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        items = kwargs.pop('items',None)
        super().__init__(*args, **kwargs)
        if items is not None:
            self.fields['items'] = forms.ModelMultipleChoiceField(queryset=items,
                error_messages={'required':'Marcá al menos una norma en la tabla.','invalid_choice':'La selección contiene una norma que no pertenece a este piloto.'})
        selected = self.data.getlist('stages') if hasattr(self.data,'getlist') else self.data.get('stages',[])
        laya_only = self.is_bound and selected==['laya']
        choices,self.catalog_warning = ([(DEFAULT_MODEL,DEFAULT_MODEL+' · comprobar al ejecutar')], '') if laya_only else model_choices()
        self.fields['model'].choices = [('', 'Elegí un modelo')]+choices
        self.fields['max_tokens'].initial = os.environ.get('JURISAI_PILOT_MAX_TOKENS',DEFAULT_MAX_TOKENS)
        self.fields['reasoning_tokens'].initial = os.environ.get('JURISAI_PILOT_REASONING_TOKENS',DEFAULT_REASONING_TOKENS)
        if laya_only:
            for name in ('model','max_tokens','reasoning_tokens'):
                self.fields[name].required = False
                self.fields[name].disabled = True
            self.fields['model'].initial = DEFAULT_MODEL
            self.fields['max_tokens'].initial = DEFAULT_MAX_TOKENS
            self.fields['reasoning_tokens'].initial = DEFAULT_REASONING_TOKENS

    def clean(self):
        data = super().clean()
        if 'items' in data and len(data['items'])>30:
            self.add_error('items','Elegí hasta 30 normas por bloque.')
        if data.get('stages'):
            data['stages'] = processing_stages({'stages':data['stages']})
        if data.get('stages')==['laya']:
            data['model'] = DEFAULT_MODEL
            data['request_settings'] = request_budget(DEFAULT_MAX_TOKENS,DEFAULT_REASONING_TOKENS)
            return data
        if 'max_tokens' in data and 'reasoning_tokens' in data:
            try:
                data['request_settings'] = request_budget(data['max_tokens'],data['reasoning_tokens'])
            except ValueError as exc:
                self.add_error('reasoning_tokens',str(exc))
        return data


class PrepareForm(ModelForm):
    run = forms.ModelChoiceField(label='Lote descargado', queryset=CrawlRun.objects.none())
    reference_required = forms.BooleanField(label='Preparar una referencia manual antes del análisis', required=False,
        help_text='Opcional. Detiene el trabajo para revisar respuestas esperadas antes de consultar al modelo.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['run'].queryset = CrawlRun.objects.filter(status__in=['ok','partial'],finished_at__isnull=False).exclude(requested_ids=[]).exclude(requested_ids=None)
        self.fields['run'].label_from_instance = lambda run: run.label


class GroupForm(forms.Form):
    run = forms.ModelChoiceField(label='Lote descargado',queryset=CrawlRun.objects.none())

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['run'].queryset = CrawlRun.objects.filter(status__in=['ok','partial'],finished_at__isnull=False).exclude(requested_ids=[]).exclude(requested_ids=None)
        self.fields['run'].label_from_instance = lambda run:run.label


def create_groups(run, username):
    ids = list(run.items.exclude(norm_id=None).filter(status__in=['completa','solo_texto_original','solo_ficha']).values_list('norm_id',flat=True))
    groups=[]
    with transaction.atomic():
        for submission,config in norm_groups(run.pk,ids):
            job,_ = CrawlJob.objects.get_or_create(submission_id=submission,defaults={'mode':'pilot',
                'start_id':min(run.requested_ids),'end_id':min(run.requested_ids),'crawl_run':run,
                'requested_by':username,'created_at':timezone.now()})
            pilot,_ = AnalysisPilot.objects.get_or_create(job=job,defaults={'crawl_run':run,'model':DEFAULT_MODEL,
                'config':config,'created_at':timezone.now()})
            groups.append(pilot)
    return groups


def latest_reviews(item, phase, user_id=None):
    query = item.evaluations.filter(phase=phase)
    if user_id is not None:
        query = query.filter(user_id=user_id)
    return {row.block: row for row in query.order_by('id')}


@staff_required
@require_http_methods(['GET','POST'])
def index(request):
    grouping = request.POST.get('action')=='groups'
    group_form = GroupForm(request.POST if grouping else None,auto_id='group_%s')
    if request.method=='POST' and grouping and group_form.is_valid():
        groups = create_groups(group_form.cleaned_data['run'],request.user.username)
        if groups:
            return redirect('review:pilot_detail',pk=groups[0].pk)
        group_form.add_error('run','Este lote no tiene normas extraídas. Revisá los errores de la descarga.')
    form = PrepareForm(request.POST if request.method=='POST' and not grouping else None, initial={
        'submission_id':uuid.uuid4(),'model':os.environ.get('JURISAI_PILOT_MODEL',DEFAULT_MODEL)})
    if request.method=='POST' and not grouping and form.is_valid():
        run = form.cleaned_data['run']
        with transaction.atomic():
            job, _ = CrawlJob.objects.get_or_create(submission_id=form.cleaned_data['submission_id'], defaults={
                'mode':'pilot','start_id':min(run.requested_ids),'end_id':min(run.requested_ids),
                'crawl_run':run,'requested_by':request.user.username,'created_at':timezone.now()})
            if job.mode != 'pilot':
                form.add_error(None,'La solicitud ya pertenece a otra tarea. Recargá el formulario.')
            else:
                pilot, _ = AnalysisPilot.objects.get_or_create(job=job, defaults={'crawl_run':run,
                    'model':form.cleaned_data['model'],'created_at':timezone.now(),
                    'config':{'contract':VERSION,'prompt':PROMPT_VERSION,'reference_required':form.cleaned_data['reference_required'],
                        'stages':form.cleaned_data['stages'],'request_settings':form.cleaned_data['request_settings']}})
                if (pilot.model!=form.cleaned_data['model'] or pilot.crawl_run_id!=run.pk or pilot.config.get('source_pilot_id')
                        or pilot.config.get('request_settings')!=form.cleaned_data['request_settings']
                        or processing_stages(pilot.config)!=form.cleaned_data['stages']):
                    form.add_error(None,'Esta solicitud ya creó otro piloto. Recargá antes de cambiar el modelo, los límites o el lote.')
                else:
                    return redirect('review:pilot_detail', pk=pilot.pk)
    return render(request,'review/pilots/index.html',{'form':form,'group_form':group_form,'pilots':AnalysisPilot.objects.select_related('job','crawl_run')[:50]},
                  status=400 if request.method=='POST' else 200)


def report_data(pilot):
    items = list(pilot.items.all())
    totals = {'confirmed':0,'corrected':0,'legal':0,'pending':0}
    by_split = {split: {'confirmed':0,'corrected':0,'legal':0,'pending':0} for split in ('holdout','development')}
    comparison = {block: {'same':0,'different':0,'unavailable':0,'extra':0,'missing':0,'matched':0} for block in ('matters','concepts','relations')}
    by_block = {block: {'label':label,'confirmed':0,'corrected':0,'legal':0} for block,label in BLOCKS.items()}
    laya_metrics = {'correct':0,'different':0,'incomplete':0,'topics_same':0,'topics_different':0}
    editing_seconds = 0
    seconds = tokens = requests = reviewed = 0
    costs = []
    for item in items:
        all_reviews = list(item.evaluations.filter(phase='result',block__in=list(BLOCKS)).order_by('id'))
        latest = {(r.user_id,r.block):r for r in all_reviews}
        reviewed += any(all(latest.get((user,block)) and latest[user,block].decision!='pending'
                            for block in BLOCKS if block!='articles') for user in {r.user_id for r in all_reviews})
        editing_seconds += sum(r.seconds for r in item.evaluations.all())
        for review in latest.values():
            totals[review.decision] += 1
            by_split[item.split][review.decision] += 1
            if review.decision in by_block[review.block]:
                by_block[review.block][review.decision] += 1
        if item.status=='done':
            for block in comparison:
                if item.metadata.get('reference_decisions',{}).get(block) not in ('confirmed','corrected'):
                    comparison[block]['unavailable'] += 1
                    continue
                def labels(value):
                    if block=='matters':
                        return sorted((v['label'].casefold(),v['primary']) for v in value)
                    if block=='concepts':
                        return sorted((v['label'].casefold(),v['role']) for v in value)
                    return sorted((v['source_unit_id'],v['direction'],v['target_type'].casefold(),v['target_number'].replace('.',''),
                                   v['target_article'],v['type'],v['scope'],v['detail']) for v in value)
                expected,actual=set(labels(item.reference[block])),set(labels(item.result[block]))
                same = expected==actual
                comparison[block]['same' if same else 'different'] += 1
                comparison[block]['matched'] += len(expected & actual)
                comparison[block]['extra'] += len(actual-expected)
                comparison[block]['missing'] += len(expected-actual)
            if item.metadata.get('reference_decisions',{}).get('relations') in ('confirmed','corrected'):
                seen = set()
                for assessment in item.laya.get('relations',[]):
                    unit = assessment['candidate']['source_unit_id']
                    if unit in seen:
                        continue
                    seen.add(unit)
                    if not assessment['complete']:
                        laya_metrics['incomplete'] += 1
                        continue
                    effects = {r['type'] for r in item.reference['relations'] if r['source_unit_id']==unit and r['type']!='cita'}
                    expected = 'ninguno' if not effects else next(iter(effects)) if len(effects)==1 else 'ambiguo'
                    laya_metrics['correct' if assessment['prediction']==expected else 'different'] += 1
            if item.metadata.get('reference_decisions',{}).get('matters') in ('confirmed','corrected') and item.laya.get('topics'):
                expected = {r['label'] for r in item.reference['matters']}
                topics = item.laya['topics']
                actual = {r['label'] for r in topics.get('discarded_model_matters',[])} or {r['label'] for r in topics['matters'] if r.get('score') is not None}
                laya_metrics['topics_same' if actual==expected else 'topics_different'] += 1
        for attempt in item.metadata.get('attempts',[]):
            usage = attempt.get('usage') or {}
            tokens += usage.get('total_tokens') or 0
            seconds += attempt.get('seconds') or 0
            requests += bool(attempt.get('requested'))
            if usage.get('cost') is not None:
                costs.append(usage['cost'])
    return {'items':items,'totals':totals,'by_split':by_split,'comparison':comparison,'by_block':by_block,
            'laya_metrics':laya_metrics,'editing_seconds':round(editing_seconds,1),
            'seconds':round(seconds,1),'tokens':tokens,'requests':requests,'reported_cost':sum(costs) if costs else None,
            'completed':sum(i.status=='done' for i in items), 'reviewed':reviewed, 'errors':sum(i.status=='error' for i in items)}


@staff_required
@require_http_methods(['GET','POST'])
def detail(request, pk):
    pilot = get_object_or_404(AnalysisPilot.objects.select_related('job','crawl_run'),pk=pk)
    error = ''
    model_form = None
    if request.method=='POST':
        with transaction.atomic():
            pilot = AnalysisPilot.objects.select_for_update().select_related('job').get(pk=pk)
            job = CrawlJob.objects.select_for_update().get(pk=pilot.job_id)
            if job.active:
                error = 'El trabajo ya está en cola o ejecutándose.'
            elif request.POST.get('action')=='compare_model' and pilot.items.exists():
                model_form = ModelForm(request.POST,items=pilot.items.all())
                if model_form.is_valid():
                    new_job,_ = CrawlJob.objects.get_or_create(submission_id=model_form.cleaned_data['submission_id'],defaults={
                        'mode':'pilot','start_id':job.start_id,'end_id':job.end_id,'crawl_run':pilot.crawl_run,
                        'requested_by':request.user.username,'created_at':timezone.now()})
                    if new_job.mode!='pilot':
                        error = 'Esta solicitud pertenece a otro trabajo. Recargá antes de continuar.'
                    else:
                        config = {k:pilot.config[k] for k in ('contract','prompt') if k in pilot.config}
                        config.update(reference_required=False,source_pilot_id=pilot.pk,
                            source_item_ids=sorted(model_form.cleaned_data['items'].values_list('pk',flat=True)),
                            stages=model_form.cleaned_data['stages'],request_settings=model_form.cleaned_data['request_settings'])
                        group_id = pilot.pk if pilot.config.get('group') else pilot.config.get('group_pilot_id')
                        if group_id:
                            config['group_pilot_id']=group_id
                        new_pilot,created = AnalysisPilot.objects.get_or_create(job=new_job,defaults={
                            'crawl_run':pilot.crawl_run,'model':model_form.cleaned_data['model'],'created_at':timezone.now(),
                            'status':'running','phase':'analyze','config':config,
                            'coverage':{**pilot.coverage,'selected':len(model_form.cleaned_data['items']),
                                'with_references':sum(bool(i.snapshot.get('candidates')) for i in model_form.cleaned_data['items']),
                                'updated':sum(i.snapshot.get('source',{}).get('version')=='actualizada' for i in model_form.cleaned_data['items'])},
                            'reference_locked_at':pilot.reference_locked_at})
                        if (new_pilot.config.get('source_pilot_id')!=pilot.pk or new_pilot.model!=model_form.cleaned_data['model']
                                or new_pilot.config.get('request_settings')!=model_form.cleaned_data['request_settings']
                                or processing_stages(new_pilot.config)!=model_form.cleaned_data['stages']
                                or new_pilot.config.get('source_item_ids')!=config['source_item_ids']):
                            error = 'Esta solicitud ya creó otro piloto. Recargá antes de cambiar las normas, etapas, modelo o límites.'
                        else:
                            if created:
                                selected_items = list(model_form.cleaned_data['items'])
                                for item in selected_items:
                                    item.laya_origin_id=item.pk
                                    if not item.laya or item.laya.get('error'):
                                        previous = AnalysisPilotItem.objects.filter(norm_id=item.norm_id,snapshot=item.snapshot).exclude(laya={}).exclude(laya__has_key='error').order_by('-pk').first()
                                        if previous:
                                            item.laya,item.laya_origin_id=previous.laya,previous.pk
                                AnalysisPilotItem.objects.bulk_create([AnalysisPilotItem(pilot=new_pilot,norm_id=item.norm_id,
                                    snapshot=item.snapshot,reference=item.reference,split=item.split,laya=item.laya,fingerprint=item.fingerprint,
                                    metadata={**{k:v for k,v in item.metadata.items() if k in ('reference_authors','reference_decisions','search_queries')},
                                        **({'laya_reused':True,'laya_source_item_id':item.laya_origin_id} if item.laya and not item.laya.get('error') else {})})
                                    for item in selected_items])
                            return redirect('review:pilot_detail',pk=new_pilot.pk)
                error = error or 'No se creó otro piloto. Revisá las normas, etapas y configuración indicadas abajo.'
            elif request.POST.get('action')=='analyze_now' and pilot.phase=='prepare' and pilot.status=='reference' and pilot.items.exists():
                # También permite continuar los pilotos creados antes de que la referencia fuera opcional.
                pilot.config['reference_required'] = False
                pilot.phase = 'analyze'
            elif request.POST.get('action')=='analyze' and pilot.phase=='prepare' and pilot.status=='reference':
                baseline = []
                for item in pilot.items.select_for_update():
                    reviews = {b:r for b,r in latest_reviews(item,'reference').items() if b in BLOCKS}
                    if set(reviews) != set(BLOCKS) or any(r.decision=='pending' for r in reviews.values()):
                        error = 'Falta revisar la referencia de una norma. Abrila desde el listado y resolvé los bloques pendientes.'
                        break
                    baseline.append((item,reviews))
                if not error:
                    for item,reviews in baseline:
                        reference = deepcopy(item.reference)
                        for block, review in reviews.items():
                            reference[block] = review.value
                        item.reference = reference
                        item.metadata['reference_decisions'] = {b:r.decision for b,r in reviews.items()}
                        item.metadata['reference_authors'] = {b:r.username for b,r in reviews.items()}
                        queries = item.evaluations.filter(phase='reference',block='search_queries').order_by('-id').first()
                        if queries:
                            item.metadata['search_queries'] = queries.value
                        item.save(update_fields=['reference','metadata'])
                    pilot.reference_locked_at = timezone.now()
                    pilot.phase = 'analyze'
            elif request.POST.get('action')=='resume' and pilot.status=='waiting':
                pass
            else:
                error = 'Esta acción no corresponde al estado actual del piloto.'
            if not error:
                job.status, job.error, job.finished_at, job.stage = 'queued','',None,'queued'
                job.heartbeat_at=job.last_activity_at=None
                job.progress={}
                job.save(update_fields=['status','error','finished_at','stage','heartbeat_at','last_activity_at','progress'])
                pilot.status = 'preparing' if pilot.phase=='prepare' else 'running'
                pilot.save(update_fields=['reference_locked_at','phase','status','config'])
                return redirect('review:pilot_detail',pk=pk)
    data = report_data(pilot)
    effective = pilot.config.get('endpoint',{}).get('request_settings',{})
    requested = pilot.config.get('request_settings') or effective
    if model_form is None and not pilot.job.active and data['items']:
        initial = {'submission_id':uuid.uuid4(),'model':pilot.model,
            'stages':['laya','llm'] if pilot.config.get('group') else ['llm'],
            'items':[] if pilot.config.get('group') else [i.pk for i in data['items']]}
        if 'max_tokens' in requested:
            initial['max_tokens'] = requested['max_tokens']
        if requested.get('reasoning'):
            initial['reasoning_tokens'] = requested['reasoning']['max_tokens']
        model_form = ModelForm(initial=initial,items=pilot.items.all())
    for item in data['items']:
        item.reference_done = sum(r.decision!='pending' for b,r in latest_reviews(item,'reference').items() if b in BLOCKS)
        item.result_done = sum(r.decision!='pending' for b,r in latest_reviews(item,'result',request.user.pk).items() if b in BLOCKS and b!='articles')
        if item.status=='done':
            from .cards import card_proposals
            _,item.card_exclusions=card_proposals(item,request.user.pk)
    data['published_cards']=ReviewCard.objects.filter(item__pilot=pilot,active=True).count()
    now = timezone.now()
    decorate_progress(pilot,data['items'])
    if pilot.config.get('group'):
        decorate_group(pilot,data['items'])
    selected_ids = model_form['items'].value() if model_form is not None else []
    data['selected_item_ids'] = [int(v) for v in selected_ids or [] if str(v).isdigit()]
    data['executions'] = AnalysisPilot.objects.filter(config__group_pilot_id=pilot.pk).select_related('job') if pilot.config.get('group') else []
    data.update(pilot=pilot,error=error,blocks=BLOCKS,model_form=model_form,request_settings=requested,
        selected_stages=processing_stages(pilot.config),processing_label=' + '.join('Laya' if s=='laya' else 'LLM' for s in processing_stages(pilot.config)),
        reasoning_omitted=bool(effective) and 'reasoning' not in effective,
        heartbeat_age=int((now-pilot.job.heartbeat_at).total_seconds()) if pilot.job.heartbeat_at else None,
        activity_age=int((now-pilot.job.last_activity_at).total_seconds()) if pilot.job.last_activity_at else None)
    return render(request,'review/pilots/detail.html',data,status=400 if error else 200)


def decorate_progress(pilot, items):
    stages = processing_stages(pilot.config)
    current = pilot.job.progress.get('pilot_current',{}) if pilot.job.active else {}
    for item in items:
        item.result_pilot_id,item.result_item_id=item.pilot_id,item.pk
        item.laya_state = ('Requiere atención' if item.laya.get('error') else
            'Reutilizado' if item.metadata.get('laya_reused') else 'Terminado') if item.laya else (
            'Pendiente' if 'laya' in stages else 'Sin resultado guardado · no se ejecutará')
        if item.laya.get('error') and 'laya' not in stages:
            item.laya_state = 'Error guardado · Laya no solicitada'
        item.llm_state = ('Reutilizada' if item.metadata.get('reused') else 'Ficha lista') if item.status=='done' else (
            'Requiere atención' if item.error else 'Pendiente' if 'llm' in stages else 'No solicitada')
        item.processing_now = current.get('item_id')==item.pk
        if item.processing_now:
            if current.get('stage')=='laya': item.laya_state='Procesando ahora'
            if current.get('stage')=='llm': item.llm_state='Procesando ahora'


def decorate_group(pilot, items):
    children = AnalysisPilotItem.objects.filter(pilot__config__group_pilot_id=pilot.pk).select_related('pilot__job').order_by('-pilot_id','-pk')
    laya, llm, latest = {}, {}, {}
    for child in children:
        decorate_progress(child.pilot,[child])
        latest.setdefault(child.norm_id,child)
        if 'laya' in processing_stages(child.pilot.config) or child.laya:
            laya.setdefault(child.norm_id,child)
        if 'llm' in processing_stages(child.pilot.config):
            llm.setdefault(child.norm_id,child)
    for item in items:
        item.laya_state=item.llm_state='Sin analizar'
        item.latest_block=latest.get(item.norm_id)
        if item.norm_id in laya:
            child=laya[item.norm_id]
            item.laya,item.laya_state=child.laya,child.laya_state
            item.processing_now=item.processing_now or child.processing_now
        if item.norm_id in llm:
            child=llm[item.norm_id]
            item.status,item.llm_state=child.status,child.llm_state
            item.result_pilot_id,item.result_item_id=child.pilot_id,child.pk
            item.error=child.error
            item.processing_now=item.processing_now or child.processing_now


@staff_required
@require_GET
def progress(request, pk):
    pilot = get_object_or_404(AnalysisPilot.objects.select_related('job','crawl_run'),pk=pk)
    items = list(pilot.items.all())
    decorate_progress(pilot,items)
    if pilot.config.get('group'):
        decorate_group(pilot,items)
    now = timezone.now()
    context = {'pilot':pilot,'items':items,'selected_stages':processing_stages(pilot.config),
        'processing_label':' + '.join('Laya' if s=='laya' else 'LLM' for s in processing_stages(pilot.config)),
        'request_settings':pilot.config.get('request_settings',{}),'live_refresh':True,
        'heartbeat_age':int((now-pilot.job.heartbeat_at).total_seconds()) if pilot.job.heartbeat_at else None,
        'activity_age':int((now-pilot.job.last_activity_at).total_seconds()) if pilot.job.last_activity_at else None}
    for item in items:
        item.reference_done = sum(r.decision!='pending' for b,r in latest_reviews(item,'reference').items() if b in BLOCKS)
        item.result_done = sum(r.decision!='pending' for b,r in latest_reviews(item,'result',request.user.pk).items() if b in BLOCKS and b!='articles')
    effective = pilot.config.get('endpoint',{}).get('request_settings',{})
    context['reasoning_omitted'] = bool(effective) and 'reasoning' not in effective
    response = JsonResponse({'active':pilot.job.active,'status':pilot.status_label,
        'progress_html':render_to_string('review/pilots/progress.html',context,request=request),
        'sample_html':render_to_string('review/pilots/sample.html',context,request=request)})
    response['Cache-Control']='no-store'
    return response


def display_facts(block, value, snapshot):
    if block=='summary':
        return [{'label':value['text'] or 'Sin resumen propuesto','evidence':value['evidence']}]
    if block=='articles':
        units = {u['id']:u for u in snapshot['units']}
        return [{'label':('Artículo ' if units[v['unit_id']]['kind']=='article' else '')+units[v['unit_id']]['label']+' · '+(v['function'] or 'Función sin preparar'),
                 'evidence':[{'unit_id':v['unit_id'],'quote':units[v['unit_id']]['text'][:1500]}]} for v in value]
    if block in ('matters','concepts'):
        return [{'label':v['label'],'detail':('Principal' if v['primary'] else 'Secundaria') if block=='matters' else ('Central' if v['role']=='central' else 'Mención'),
                 'evidence':v['evidence']} for v in value]
    facts = []
    for v in value:
        matches = list(Norm.objects.annotate(clean_number=Replace('number',Value('.'),Value(''),output_field=TextField())).filter(
            norm_type__iexact=v['target_type'],clean_number=v['target_number'].replace('.',''))[:2])
        facts.append({'label':v['type'].capitalize()+' · '+v['target_type']+' '+v['target_number'],
            'detail':('Esta norma → referida' if v['direction']=='outgoing' else 'Referida → esta norma')+
                (' · artículo '+v['target_article'] if v['target_article'] else '')+' · '+v['scope']+
                (' · '+v['detail'] if v['detail'] else ''), 'evidence':v['evidence'],
            'resolution':'Norma vinculada' if len(matches)==1 else 'Identidad ambigua' if matches else 'Esperando incorporación de la norma referida'})
    return facts


@staff_required
@require_GET
def item_detail(request, pk, item_id):
    item = get_object_or_404(AnalysisPilotItem.objects.select_related('pilot'),pk=item_id,pilot_id=pk)
    return render_item(request,item)


def render_item(request,item,bound=None,error='',status=200):
    phase = 'reference' if (request.GET.get('reference')=='1' or request.POST.get('phase')=='reference') and item.pilot.phase=='prepare' else 'result'
    proposal = item.result if phase=='result' else item.reference
    reviews = latest_reviews(item,phase,request.user.pk)
    blocks = []
    if proposal:
        for block,label in BLOCKS.items():
            previous = reviews.get(block)
            value = previous.value if previous else proposal[block]
            form = BlockForm(block,value,item.snapshot,initial={
                'revision':previous.revision if previous else 0,'decision':previous.decision if previous else 'pending',
                'notes':previous.notes if previous else ''}, prefix=block)
            if bound is not None and bound.block==block:
                form=bound
            facts = display_facts(block,value,item.snapshot)
            proofs = {}
            for fact in facts:
                for evidence in fact.get('evidence',[]):
                    proof = proofs.setdefault((evidence['unit_id'],evidence['quote']),{**evidence,'labels':[]})
                    proof['labels'].append(fact['label'])
            blocks.append({'key':block,'label':label,'form':form,'facts':facts,'evidence':list(proofs.values()),
                'baseline':display_facts(block,item.reference[block],item.snapshot) if phase=='result' and item.pilot.reference_locked_at else None,
                'previous':previous})
    ids = list(item.pilot.items.values_list('id',flat=True))
    position = ids.index(item.pk)
    return render(request,'review/pilots/item.html',{'item':item,'pilot':item.pilot,'blocks':blocks,'phase':phase,
        'previous_item':ids[position-1] if position else None,'next_item':ids[position+1] if position+1<len(ids) else None,
        'position':position+1,'total':len(ids),'history':item.evaluations.order_by('-id')[:30],
        'queries_revision':reviews['search_queries'].revision if 'search_queries' in reviews else 0,
        'queries_text':'\n'.join(reviews['search_queries'].value if 'search_queries' in reviews else item.metadata.get('search_queries',[])),
        'source_when':parse_datetime(item.snapshot['source']['retrieved_at']),
        'resources':analysis_resources(item.snapshot.get('resources_not_checked',[])),
        'reference_query':'?reference=1' if phase=='reference' else '', 'error':error},status=status)


@staff_required
@require_http_methods(['POST'])
def evaluate(request, pk, item_id):
    with transaction.atomic():
        pilot = get_object_or_404(AnalysisPilot.objects.select_for_update(),pk=pk)
        item = get_object_or_404(AnalysisPilotItem.objects.select_for_update(),pk=item_id,pilot=pilot)
        phase = request.POST.get('phase')
        block = request.POST.get('block')
        if block not in [*BLOCKS,'search_queries'] or phase not in ('reference','result'):
            return JsonResponse({'error':'Bloque o etapa de revisión inválidos.'},status=400)
        if (phase=='reference' and (pilot.reference_locked_at or pilot.phase!='prepare')) or (phase=='result' and item.status!='done'):
            return JsonResponse({'error':'Esta referencia está congelada o el resultado todavía no está listo.'},status=409)
        proposal = item.reference if phase=='reference' else item.result
        previous = item.evaluations.filter(phase=phase,block=block,user_id=request.user.pk).order_by('-revision').first()
        if block=='search_queries':
            if phase!='reference':
                return JsonResponse({'error':'Las consultas se fijan junto con la referencia.'},status=409)
            class QueriesForm(forms.Form):
                revision = forms.IntegerField(min_value=0)
                text = forms.CharField(required=False,max_length=5000)
            form = QueriesForm(request.POST)
        else:
            value = previous.value if previous else proposal[block]
            form = BlockForm(block,value,item.snapshot,request.POST,prefix=block)
        if not form.is_valid():
            if request.headers.get('X-Requested-With')=='XMLHttpRequest' or block=='search_queries':
                return JsonResponse({'error':'Revisá los campos señalados.','fields':form.errors.get_json_data()},status=400)
            return render_item(request,item,bound=form,error='Corregí el bloque señalado antes de continuar.',status=400)
        revision = previous.revision if previous else 0
        if revision != form.cleaned_data['revision']:
            return JsonResponse({'error':'El bloque cambió en otra pestaña. Tu edición sigue aquí; recargá antes de continuar.'},status=409)
        data = form.cleaned_data
        if block=='search_queries':
            data.update(decision='corrected',value=[q.strip() for q in data['text'].splitlines() if q.strip()],notes='')
        review = AnalysisPilotEvaluation.objects.create(item=item,phase=phase,block=block,user_id=request.user.pk,
            username=request.user.username,decision=data['decision'],value=data['value'],
            notes=data['notes'],revision=revision+1,created_at=timezone.now(),seconds=(data.get('elapsed_ms') or 0)/1000)
    if request.headers.get('X-Requested-With')=='XMLHttpRequest':
        return JsonResponse({'revision':review.revision,'message':'Guardado','decision':review.decision})
    return redirect(reverse('review:pilot_item',kwargs={'pk':pk,'item_id':item_id})+('?reference=1' if phase=='reference' else ''))


@staff_required
@require_GET
def source(request, pk, item_id):
    item = get_object_or_404(AnalysisPilotItem,pk=item_id,pilot_id=pk)
    response = HttpResponse(item.snapshot['source']['html'],content_type='text/html; charset=utf-8')
    response['Content-Security-Policy'] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    response['X-Frame-Options'] = 'SAMEORIGIN'
    return response


@staff_required
@require_GET
def export(request, pk):
    pilot = get_object_or_404(AnalysisPilot,pk=pk)
    items = [{'id':item.pk,'snapshot':item.snapshot,'reference':item.reference,'result':item.result,
              'laya':item.laya,'metadata':item.metadata,'split':item.split,'status':item.status,'error':item.error,
              'evaluations':list(item.evaluations.values('phase','block','username','decision','value','notes','revision','seconds','created_at'))}
             for item in pilot.items.all()]
    return JsonResponse({'pilot':pilot.pk,'model':pilot.model,'config':pilot.config,'coverage':pilot.coverage,'items':items},
                        json_dumps_params={'ensure_ascii':False},headers={'Content-Disposition':f'attachment; filename="jurisai-pilot-{pk}.json"'})
