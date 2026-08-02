from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("clients/", views.client_list, name="client_list"),
    path("clients/nouveau/", views.client_create, name="client_create"),

    path("ventes/", views.sale_list, name="sale_list"),
    path("ventes/nouvelle/", views.sale_create, name="sale_create"),
    path("ventes/<uuid:sale_id>/", views.sale_detail, name="sale_detail"),
    path("ventes/<uuid:sale_id>/confirmer/", views.sale_confirm, name="sale_confirm"),
    path("ventes/<uuid:sale_id>/facturer/", views.sale_generate_invoice, name="sale_generate_invoice"),

    path("factures/", views.invoice_list, name="invoice_list"),
    path("devis/nouveau/", views.invoice_create, name="invoice_create"),
    path("factures/<uuid:invoice_id>/", views.invoice_detail, name="invoice_detail"),
    path("factures/<uuid:invoice_id>/valider/", views.invoice_validate, name="invoice_validate"),
    path("factures/<uuid:invoice_id>/paiement/", views.payment_create, name="payment_create"),
    path("factures/<uuid:invoice_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
]
