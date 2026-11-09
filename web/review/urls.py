from django.urls import path

from . import crawler, validators, views, workflow, draft_review, pilots


app_name = "review"

urlpatterns = [
    path("", views.home, name="home"),
    path("trabajo/", views.dashboard, name="dashboard"),
    path("validar/", workflow.queue, name="queue"),
    path("validar/guia/", workflow.guide, name="guide"),
    path("validar/<int:pk>/", workflow.task_detail, name="task"),
    path("validar/<int:pk>/finalizar/", workflow.validator_required(draft_review.finalize), name="finalize"),
    path("validar/<int:pk>/fuente/<int:source_id>/", workflow.copy_source, name="copy_source"),
    path("gestion/", workflow.management, name="management"),
    path('gestion/pilotos/', pilots.index, name='pilots'),
    path('gestion/pilotos/<int:pk>/', pilots.detail, name='pilot_detail'),
    path('gestion/pilotos/<int:pk>/exportar/', pilots.export, name='pilot_export'),
    path('gestion/pilotos/<int:pk>/normas/<int:item_id>/', pilots.item_detail, name='pilot_item'),
    path('gestion/pilotos/<int:pk>/normas/<int:item_id>/evaluar/', pilots.evaluate, name='pilot_evaluate'),
    path('gestion/pilotos/<int:pk>/normas/<int:item_id>/fuente/', pilots.source, name='pilot_source'),
    path("gestion/validadores/", validators.index, name="validators"),
    path("gestion/validadores/nuevo/", validators.create, name="validator_create"),
    path("gestion/validadores/<int:pk>/", validators.detail, name="validator_detail"),
    path("gestion/descargas/", crawler.jobs, name="crawl_jobs"),
    path("gestion/descargas/<int:pk>/", crawler.job_detail, name="crawl_job"),
    path("gestion/leyes/<int:pk>/", workflow.law_reviews, name="law_reviews"),
    path("explorar/", views.norm_search, name="norm_search"),
    path("normas/<int:pk>/", views.norm_detail, name="norm_detail"),
    path("changes/", views.change_list, name="list"),
    path("changes/<int:pk>/", views.change_detail, name="detail"),
    path("changes/<int:pk>/review/", views.review_change, name="review"),
]
