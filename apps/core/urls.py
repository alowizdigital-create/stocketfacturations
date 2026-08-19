from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("s/<str:code>/", views.short_link_redirect, name="short_link"),
]
