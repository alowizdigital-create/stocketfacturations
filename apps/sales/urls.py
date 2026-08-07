from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("clients/", views.client_list, name="client_list"),
    path("clients/rechercher/", views.client_search, name="client_search"),
    path("clients/nouveau/", views.client_create, name="client_create"),
    path("clients/importer/", views.client_import, name="client_import"),

    path("ventes/", views.sale_list, name="sale_list"),
    path("ventes/nouvelle/", views.sale_create, name="sale_create"),
    path("ventes/<uuid:sale_id>/", views.sale_detail, name="sale_detail"),
    path("ventes/<uuid:sale_id>/confirmer/", views.sale_confirm, name="sale_confirm"),
    path("ventes/<uuid:sale_id>/facturer/", views.sale_generate_invoice, name="sale_generate_invoice"),

    path("factures/", views.invoice_list, name="invoice_list"),
    path("devis/", views.devis_list, name="devis_list"),
    path("devis/nouveau/", views.invoice_create, name="invoice_create"),
    path("factures/<uuid:invoice_id>/", views.invoice_detail, name="invoice_detail"),
    path("factures/<uuid:invoice_id>/valider/", views.invoice_validate, name="invoice_validate"),
    path("factures/<uuid:invoice_id>/paiement/", views.payment_create, name="payment_create"),
    path("factures/<uuid:invoice_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("factures/<uuid:invoice_id>/whatsapp/", views.invoice_send_whatsapp, name="invoice_send_whatsapp"),

    # Liens publics (sans connexion), utilisés pour le partage WhatsApp.
    path("partage/factures/<uuid:invoice_id>/pdf/", views.invoice_public_pdf, name="invoice_public_pdf"),
    path("partage/factures/<uuid:invoice_id>/photos/", views.invoice_public_gallery, name="invoice_public_gallery"),
    path("partage/factures/<uuid:invoice_id>/", views.invoice_public_view, name="invoice_public_view"),
]
