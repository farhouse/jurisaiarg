from django.urls import path

from . import views


app_name = "review"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("explorar/", views.norm_search, name="norm_search"),
    path("normas/<int:pk>/", views.norm_detail, name="norm_detail"),
    path("changes/", views.change_list, name="list"),
    path("changes/<int:pk>/", views.change_detail, name="detail"),
    path("changes/<int:pk>/review/", views.review_change, name="review"),
]
