from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("inscription/", views.signup, name="signup"),
    path("boutiques/", views.choose_boutique, name="choose_boutique"),
    path("boutiques/<uuid:boutique_id>/choisir/", views.set_boutique, name="set_boutique"),
]
