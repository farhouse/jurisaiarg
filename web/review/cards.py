"""Tarjetas de pilotos habilitados. No escriben datos jurídicos ni votos anteriores."""
from copy import deepcopy
import uuid

from django import forms
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from .models import AnalysisPilot, ReviewCard, CardResponse, Norm
from .pilot_contract import analysis_resources, fingerprint, validate_result
from .workflow import validator_required, staff_required


def active_answers():
    return CardResponse.objects.filter(action='answer').exclude(
        pk__in=CardResponse.objects.filter(action='undo').values('answer_id'))


def card_proposals(item, user_id=None):
    snapshot, result = item.snapshot, deepcopy(item.result)
    reviews = {r.block:r for r in item.evaluations.filter(phase='result',user_id=user_id,
        block__in=['summary','concepts']).order_by('id')} if user_id else {}
    for block,review in reviews.items():
        if review.decision in ('confirmed','corrected'): result[block]=review.value
    try:
        validate_result(result, snapshot)
    except (ValueError, KeyError, TypeError):
        return [], ['La ficha no cumple el contrato o sus evidencias.']
    proposals = [('topic', {'label':c['label'], 'evidence':c['evidence']}) for c in result['concepts']]
    reasons = []
    document = next((u['text'] for u in snapshot['units'] if u['id']=='document'), '')
    summary = result['summary']
    if not summary['text'].strip(): reasons.append('Resumen ausente.')
    if len(document)>4000: reasons.append('La norma supera 4.000 caracteres para revisar su resumen.')
    if len(summary['text'])>600: reasons.append('El resumen supera 600 caracteres.')
    if snapshot.get('integrity',{}).get('status')!='coincide': reasons.append('La copia todavía no está comprobada.')
    if analysis_resources(snapshot.get('resources_not_checked',[])): reasons.append('Hay anexos o recursos pendientes de comprobar.')
    if not document.strip(): reasons.append('Falta el texto completo para revisar el resumen.')
    if reviews.get('summary') and reviews['summary'].decision=='legal': reasons.append('El resumen requiere criterio jurídico.')
    if not reasons: proposals.append(('summary', summary))
    if reviews.get('concepts') and reviews['concepts'].decision=='legal':
        proposals=[(kind,value) for kind,value in proposals if kind!='topic']
        reasons.append('Los conceptos requieren criterio jurídico.')
    for kind,proposal in proposals:
        review=reviews.get('summary' if kind=='summary' else 'concepts')
        if review and review.decision in ('confirmed','corrected'):
            proposal['review']={'id':review.pk,'username':review.username,'user_id':review.user_id}
    return proposals, reasons


@transaction.atomic
def publish(pilot_id, enabled, user_id=None):
    pilot = AnalysisPilot.objects.select_for_update().get(pk=pilot_id)
    if enabled:
        for item in pilot.items.filter(status='done'):
            proposals, _ = card_proposals(item,user_id)
            keys = []
            for kind, proposal in proposals:
                snapshot = deepcopy(item.snapshot)
                snapshot['provenance'] = {'model':pilot.model,'contract':pilot.config.get('contract'),
                    'prompt':pilot.config.get('prompt'), 'metadata':{k:item.metadata[k] for k in
                    ('model','provider','request_id','contract','prompt') if k in item.metadata}}
                key = fingerprint({'kind':kind,'proposal':{k:v for k,v in proposal.items() if k!='review'},'source':snapshot['source']['hash'],
                    'units':snapshot['units'],'identity':snapshot['identity'],
                    'provenance':{k:snapshot['provenance'][k] for k in ('model','contract','prompt')}})
                keys.append(key)
                ReviewCard.objects.get_or_create(item=item, fingerprint=key, defaults={
                    'kind':kind,'snapshot':snapshot,'proposal':proposal,'created_at':timezone.now()})
            item.cards.exclude(fingerprint__in=keys).update(active=False)
            item.cards.filter(fingerprint__in=keys).update(active=True)
        ReviewCard.objects.filter(item__pilot=pilot).exclude(item__status='done').update(active=False)
    else:
        ReviewCard.objects.filter(item__pilot=pilot).update(active=False)
    pilot.config['cards_enabled'] = enabled
    pilot.save(update_fields=['config'])


def last_answer(user):
    return active_answers().filter(user_id=user.pk).select_related('card').order_by('-id').first()


def next_card(user):
    own = active_answers().filter(user_id=user.pk).values('card_id')
    count = active_answers().filter(card_id=OuterRef('pk')).exclude(decision='skip').values('card_id').annotate(n=Count('pk')).values('n')
    return ReviewCard.objects.filter(active=True).exclude(pk__in=own).annotate(
        votes=Coalesce(Subquery(count),Value(0))).order_by('votes','id').first()


def frame_data(request, card=None, force=False):
    last = last_answer(request.user)
    if not force: card = next_card(request.user)
    evidence = []
    if card:
        units = {u['id']:u for u in card.snapshot['units']}
        for proof in card.proposal['evidence']:
            unit = units[proof['unit_id']]
            evidence.append({**proof,'label':('Artículo ' if unit['kind']=='article' else '')+unit['label'],'text':unit['text']})
    note = last.events.filter(action='note').order_by('-id').first() if last else None
    return {'card':card,'evidence':evidence,'last':last,'last_note':note.notes if note else '',
        'answer_token':uuid.uuid4(),'undo_token':uuid.uuid4(),'note_token':uuid.uuid4()}


@validator_required
@require_GET
@never_cache
def index(request):
    if request.GET.get('restore','').isdigit():
        card = ReviewCard.objects.filter(pk=request.GET['restore'],active=True).first()
        if card and CardResponse.objects.filter(user_id=request.user.pk,card=card,action='undo').exists() and not active_answers().filter(user_id=request.user.pk,card=card).exists():
            return render(request,'review/cards/index.html',frame_data(request,card,force=True))
    return render(request,'review/cards/index.html',frame_data(request))


@validator_required
@require_GET
@never_cache
def next_view(request):
    response = render(request,'review/cards/frame.html',frame_data(request))
    response['Cache-Control'] = 'no-store'
    return response


class ResponseForm(forms.Form):
    request_id = forms.UUIDField()
    decision = forms.ChoiceField(choices=[('correct','Correcto'),('incorrect','Con problemas'),('skip','No sé')],required=False)
    answer_id = forms.IntegerField(min_value=1,required=False)
    notes = forms.CharField(max_length=2000,required=False)


class ResponseConflict(Exception):
    pass


@validator_required
@require_POST
def respond(request, pk, action='answer'):
    form = ResponseForm(request.POST)
    error, status, restore = '', 400, None
    if form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                # Serializa las pestañas de la misma persona, sin impedir votos independientes.
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                existing = CardResponse.objects.filter(user_id=request.user.pk,request_id=data['request_id']).first()
                decision = data['decision'] if action=='answer' else ''
                answer_id = data['answer_id'] if action!='answer' else None
                notes = data['notes'].strip() if action=='note' else ''
                if existing:
                    if (existing.card_id,existing.action,existing.decision,existing.answer_id,existing.notes)!=(pk,action,decision,answer_id,notes):
                        raise ResponseConflict('La solicitud ya fue usada para otra respuesta. Recargá antes de continuar.')
                    if action=='undo' and existing.card.active and not active_answers().filter(user_id=request.user.pk,card=existing.card).exists(): restore = existing.card
                else:
                    card = get_object_or_404(ReviewCard.objects.select_for_update(),pk=pk)
                    if action=='answer':
                        if not decision: raise ResponseConflict('Elegí una de las tres respuestas.')
                        if not card.active: raise ResponseConflict('Esta tarjeta fue retirada. Buscá una nueva para continuar.')
                        if active_answers().filter(card=card,user_id=request.user.pk).exists():
                            raise ResponseConflict('Ya respondiste esta tarjeta en otra pestaña. Tu respuesta se conserva; buscá la siguiente.')
                    else:
                        target = active_answers().filter(pk=answer_id,card=card,user_id=request.user.pk).first()
                        if not target: raise ResponseConflict('La respuesta ya fue deshecha o no pertenece a tu cuenta.')
                        if action=='undo' and last_answer(request.user).pk!=target.pk:
                            raise ResponseConflict('Solo podés deshacer tu última respuesta. Buscá una nueva tarjeta para actualizar el estado.')
                        if action=='undo' and card.active: restore=card
                    CardResponse.objects.create(card=card,user_id=request.user.pk,username=request.user.username,
                        action=action,decision=decision,answer_id=answer_id,notes=notes,
                        request_id=data['request_id'],created_at=timezone.now())
            if request.headers.get('X-Requested-With')=='XMLHttpRequest':
                if action=='note': return JsonResponse({'message':'Comentario guardado.','request_id':str(uuid.uuid4())})
                html = render_to_string('review/cards/frame.html',frame_data(request,restore,force=restore is not None),request=request)
                return JsonResponse({'html':html,'message':{'answer':'Respuesta guardada.','undo':'Respuesta deshecha.','note':'Comentario guardado.'}[action]})
            if restore: return redirect(reverse('review:queue')+f'?restore={restore.pk}')
            return redirect('review:queue')
        except ResponseConflict as exc:
            error,status = str(exc),409
    else:
        error = 'Revisá el comentario (hasta 2.000 caracteres) o recargá si la solicitud quedó desactualizada.'
    if request.headers.get('X-Requested-With')=='XMLHttpRequest':
        return JsonResponse({'error':error},status=status)
    card=get_object_or_404(ReviewCard,pk=pk)
    return render(request,'review/cards/index.html',{**frame_data(request,card,force=True),'error':error},status=status)


@validator_required
@require_GET
def source(request, pk):
    card=get_object_or_404(ReviewCard,pk=pk,active=True)
    response=HttpResponse(card.snapshot['source']['html'],content_type='text/html; charset=utf-8')
    response['Content-Security-Policy']="sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    response['X-Frame-Options']='SAMEORIGIN'
    return response


@staff_required
@require_POST
def publication(request, pk):
    get_object_or_404(AnalysisPilot,pk=pk)
    if request.POST.get('action') not in ('publish','withdraw'):
        return HttpResponse('Acción inválida.',status=400)
    publish(pk,request.POST['action']=='publish',request.user.pk)
    messages.success(request,'Tarjetas actualizadas.' if request.POST['action']=='publish' else 'Muestra retirada. Las respuestas y el historial se conservan.')
    return redirect('review:pilot_detail',pk=pk)


@staff_required
@require_GET
def report(request):
    class Filters(forms.Form):
        norm = forms.IntegerField(min_value=1,required=False)
        user = forms.IntegerField(min_value=1,required=False)
        pilot = forms.IntegerField(min_value=1,required=False)
    filters=Filters(request.GET)
    if not filters.is_valid(): return HttpResponse('Filtro inválido.',status=400)
    cards=ReviewCard.objects.select_related('item__pilot')
    for field,lookup in [('norm','item__norm_id'),('pilot','item__pilot_id')]:
        if filters.cleaned_data[field]: cards=cards.filter(**{lookup:filters.cleaned_data[field]})
    laws=Paginator(Norm.objects.filter(pk__in=cards.values('item__norm_id')).order_by('infoleg_id'),25).get_page(request.GET.get('page'))
    selected=list(cards.filter(item__norm_id__in=[n.pk for n in laws]).order_by('id'))
    responses=active_answers().filter(card_id__in=[c.pk for c in selected])
    if filters.cleaned_data['user']: responses=responses.filter(user_id=filters.cleaned_data['user'])
    counts={}
    for vote in responses:
        row=counts.setdefault(vote.card_id,{'correct':0,'incorrect':0,'skip':0,'users':set()})
        row[vote.decision]+=1;row['users'].add(vote.user_id)
    for law in laws:
        law.card_rows=[];law.card_totals={'active':0,'correct':0,'incorrect':0,'skip':0,'disagreements':0,'unanswered':0};people=set()
        for card in selected:
            if card.item.norm_id!=law.pk:continue
            card.counts=counts.get(card.pk,{'correct':0,'incorrect':0,'skip':0,'users':set()})
            card.disagreement=bool(card.counts['correct'] and card.counts['incorrect'])
            law.card_rows.append(card)
            if card.active:
                people.update(card.counts['users']);law.card_totals['active']+=1
                for key in ('correct','incorrect','skip'):law.card_totals[key]+=card.counts[key]
                law.card_totals['disagreements']+=card.disagreement
                law.card_totals['unanswered']+=not (card.counts['correct'] or card.counts['incorrect'])
        law.people=len(people)
    history=CardResponse.objects.filter(card__in=cards).select_related('card__item__norm').order_by('-id')
    if filters.cleaned_data['user']:history=history.filter(user_id=filters.cleaned_data['user'])
    params=request.GET.copy();params.pop('page',None);params.pop('events_page',None)
    return render(request,'review/cards/report.html',{'laws':laws,'history':Paginator(history,30).get_page(request.GET.get('events_page')),
        'params':params.urlencode(),'filtered_norm':filters.cleaned_data['norm'],'filtered_user':filters.cleaned_data['user'],
        'filtered_pilot':filters.cleaned_data['pilot']})
