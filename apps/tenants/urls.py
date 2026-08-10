from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("inscription/", views.signup, name="signup"),
    path("boutiques/", views.choose_boutique, name="choose_boutique"),
    path("boutiques/<uuid:boutique_id>/choisir/", views.set_boutique, name="set_boutique"),
    path("equipe/", views.staff_list, name="staff_list"),
    path("equipe/nouveau/", views.staff_create, name="staff_create"),
    path("equipe/<uuid:membership_id>/modifier/", views.staff_update, name="staff_update"),
    path("parametres/", views.company_settings, name="company_settings"),
    path("parametres/taux-de-change/", views.exchange_rate_list, name="exchange_rate_list"),
    path("parametres/taux-de-change/<uuid:rate_id>/supprimer/", views.exchange_rate_delete, name="exchange_rate_delete"),
    path("parametres/jeton/", views.boutique_token, name="boutique_token"),
    path("parametres/jeton/generer/", views.boutique_token_generate, name="boutique_token_generate"),
    path("abonnement/", views.subscription_view, name="subscription"),
]
