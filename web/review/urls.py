from django.urls import path

from . import crawler, views, workflow


app_name = "review"

urlpatterns = [
    path("", views.home, name="home"),
    path("trabajo/", views.dashboard, name="dashboard"),
    path("validar/", workflow.queue, name="queue"),
    path("validar/guia/", workflow.guide, name="guide"),
    path("validar/<int:pk>/", workflow.task_detail, name="task"),
    path("gestion/", workflow.management, name="management"),
    path("gestion/descargas/", crawler.jobs, name="crawl_jobs"),
    path("gestion/descargas/<int:pk>/", crawler.job_detail, name="crawl_job"),
    path("gestion/leyes/<int:pk>/", workflow.law_reviews, name="law_reviews"),
    path("explorar/", views.norm_search, name="norm_search"),
    path("normas/<int:pk>/", views.norm_detail, name="norm_detail"),
    path("changes/", views.change_list, name="list"),
    path("changes/<int:pk>/", views.change_detail, name="detail"),
    path("changes/<int:pk>/review/", views.review_change, name="review"),
]
