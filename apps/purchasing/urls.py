from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.purchase_list, name="purchase_list"),
    path("nouvelle/", views.purchase_create, name="purchase_create"),
    path("<uuid:purchase_id>/", views.purchase_detail, name="purchase_detail"),
    path("depenses/", views.expense_list, name="expense_list"),
    path("depenses/nouvelle/", views.expense_create, name="expense_create"),
    path("depenses/<uuid:expense_id>/annuler/", views.expense_cancel, name="expense_cancel"),
    path("fournisseurs/", views.supplier_list, name="supplier_list"),
    path("fournisseurs/nouveau/", views.supplier_create, name="supplier_create"),
    path("fournisseurs/creation-rapide/", views.supplier_quick_create, name="supplier_quick_create"),
    path("fournisseurs/<uuid:supplier_id>/modifier/", views.supplier_update, name="supplier_update"),
]
