"""One norm/version dossier; global relations remain open to later batches."""
from django.db.models import Q
from .models import LegalChange, NormReference, NormRelation, ReviewDraft, ReviewTask
from . import copy_review


def context(entry, root_id, request):
    from .workflow import review_url
    root=ReviewTask.objects.select_related('norm','crawl_run').get(pk=root_id)
    copy=copy_review.document_context(root,None,request,review_url(root_id,'copy',root_id))
    active=ReviewTask.objects.filter(norm_id=root.norm_id,crawl_run_id=root.crawl_run_id,superseded=False)
    topics=active.filter(model_result__category='temas').filter(Q(model_result__version=copy['version'])|Q(model_result__version__isnull=True)).order_by('-id').first()
    issues=[]
    for task in active.filter(decision='revision'):
        if task.model_result.get('requires_processing'):
            continue
        if task.kind=='indice':
            documents=[c for c in copy['checks'] if c['proof']['status']=='diferencias' and not c['complete']]
            for control in documents:
                issues.append({'label':'Diferencias en '+control['version_label'].lower(), 'reasons':['Compará únicamente los fragmentos señalados. Una decisión cubre el documento completo.'], 'url':control['url']})
        elif task.review_status=='pendiente':
            issues.append({'label':'Revisar la propuesta temática' if task.model_result.get('category')=='temas' else 'Resolver una relación concreta',
                'reasons':task.reasons_display,'url':review_url(root_id,'topics' if task.model_result.get('category')=='temas' else 'relations',task.pk)})
    links=[]
    for ref in NormReference.objects.filter(Q(source_norm_id=root.norm_id)|Q(target_norm_id=root.norm_id)).select_related('source_norm','target_norm','source_version','source_article_version__article').order_by('-id')[:100]:
        links.append({'source':ref.source_norm.display_reference,'target':ref.target_reference,
            'type':{'cita':'Cita o remisión','sustituye':'Sustituye','deroga':'Deroga','incorpora':'Incorpora','modifica':'Modifica','ambiguo':'Efecto por determinar'}.get(ref.relation_type,ref.relation_type),
            'article':ref.target_article,'resolution':{'pendiente':'Esperando incorporación de la norma referida','ambigua':'Identidad ambigua; requiere revisión','resuelta':'Norma vinculada'}.get(ref.resolution,ref.resolution),
            'evidence':ref.evidence_text,'incoming':ref.target_norm_id==root.norm_id,
            'version':copy_review.VERSIONS.get(ref.source_version.version_type,ref.source_version.version_type),
            'target_url':ref.target_norm.infoleg_url if ref.target_norm_id else None,'review_status':ref.review_status})
    for change in LegalChange.objects.filter(Q(amending_norm_id=root.norm_id)|Q(affected_norm_id=root.norm_id)).select_related('amending_norm','affected_norm','affected_article').order_by('-id')[:100]:
        links.append({'source':change.amending_norm.display_reference if change.amending_norm_id else change.amending_reference_raw,
            'target':change.affected_norm.display_reference,'article':change.affected_article.article_label,
            'type':change.change_type,'resolution':'Detectada en nota de InfoLEG','evidence':change.evidence_text,
            'incoming':change.affected_norm_id==root.norm_id,'version':'Nota editorial','review_status':change.review_status})
    for link in NormRelation.objects.filter(Q(source_norm_id=root.norm_id)|Q(target_norm_id=root.norm_id)).select_related('source_norm','target_norm').order_by('-id')[:100]:
        links.append({'source':link.source_norm.display_reference,'target':link.target_norm.display_reference,
            'type':'Modifica o complementa · vínculo general de InfoLEG','resolution':'Alcance específico no determinado por este vínculo',
            'evidence':link.evidence_text,'incoming':link.target_norm_id==root.norm_id,'review_status':link.review_status})
    draft=ReviewDraft.objects.filter(task_id=root_id,user_id=request.user.pk).first()
    archived=[]
    for item in (draft.data.get('archived_items',{}) if draft else {}).values():
        fields=item.get('fields',{})
        archived.append({'status':(fields.get('status') or ['Sin decisión'])[0], 'notes':(fields.get('notes') or [''])[0], 'query':item.get('query',{})})
    return {'root':root,'copy':copy,'topics':topics,'issues':issues,'links':links,'archived_draft':archived,
            'processing_needed':root.model_result.get('requires_processing') or not topics or topics.model_result.get('requires_processing'),
            'topic_url':review_url(root_id,'topics',topics.pk) if topics else None,
            'copy_url':copy['control']['url'] if copy['control'] else review_url(root_id,'copy')}
