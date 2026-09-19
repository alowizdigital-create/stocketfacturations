from django.urls import path

from . import platform_views as views

app_name = "platform_admin"

urlpatterns = [
    path("", views.platform_dashboard, name="dashboard"),
    path("utilisateurs/", views.platform_user_list, name="user_list"),
    path("utilisateurs/<uuid:user_id>/", views.platform_user_edit, name="user_edit"),
]
