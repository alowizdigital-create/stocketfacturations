from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("clients/", views.client_list, name="client_list"),
    path("clients/rechercher/", views.client_search, name="client_search"),
    path("clients/nouveau/", views.client_create, name="client_create"),
    path("clients/creation-rapide/", views.client_quick_create, name="client_quick_create"),
    path("clients/importer/", views.client_import, name="client_import"),
    path("clients/<uuid:client_id>/releve/", views.client_statement, name="client_statement"),
    path("clients/<uuid:client_id>/relancer/", views.client_remind, name="client_remind"),
    path("relances/", views.debtor_list, name="debtor_list"),

    path("ventes/", views.sale_list, name="sale_list"),
    path("ventes/nouvelle/", views.sale_create, name="sale_create"),
    path("ventes/<uuid:sale_id>/", views.sale_detail, name="sale_detail"),
    path("ventes/<uuid:sale_id>/confirmer/", views.sale_confirm, name="sale_confirm"),
    path("ventes/<uuid:sale_id>/facturer/", views.sale_generate_invoice, name="sale_generate_invoice"),

    path("rapports/marge/", views.margin_report, name="margin_report"),

    path("factures/", views.invoice_list, name="invoice_list"),
    path("devis/", views.devis_list, name="devis_list"),
    path("devis/nouveau/", views.invoice_create, name="invoice_create"),
    path("devis/<uuid:invoice_id>/modifier/", views.devis_update, name="devis_update"),
    path("commandes/", views.commande_list, name="commande_list"),
    path("commandes/rechercher/", views.commande_search, name="commande_search"),
    path("commandes/export/pdf/", views.commande_export_pdf, name="commande_export_pdf"),
    path("commandes/export/excel/", views.commande_export_excel, name="commande_export_excel"),
    path("commandes/nouvelle/", views.commande_create, name="commande_create"),
    path("commandes/<uuid:invoice_id>/modifier/", views.commande_update, name="commande_update"),
    path("commandes/<uuid:invoice_id>/valider/", views.commande_generate_invoice, name="commande_generate_invoice"),
    path("commandes/<uuid:invoice_id>/livrer/", views.commande_mark_delivered, name="commande_mark_delivered"),
    path("commandes/<uuid:invoice_id>/avancer/", views.commande_advance, name="commande_advance"),
    path("commandes/<uuid:invoice_id>/remettre-en-cours/", views.commande_mark_en_cours, name="commande_mark_en_cours"),
    path("factures/<uuid:invoice_id>/", views.invoice_detail, name="invoice_detail"),
    path("factures/<uuid:invoice_id>/valider/", views.invoice_validate, name="invoice_validate"),
    path("devis/<uuid:invoice_id>/facturer/", views.devis_generate_invoice, name="devis_generate_invoice"),
    path("factures/<uuid:invoice_id>/paiement/", views.payment_create, name="payment_create"),
    path("factures/<uuid:invoice_id>/relancer/", views.invoice_remind, name="invoice_remind"),
    path("factures/<uuid:invoice_id>/encaisser/", views.invoice_pay, name="invoice_pay"),
    path("factures/<uuid:invoice_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("factures/<uuid:invoice_id>/whatsapp/", views.invoice_send_whatsapp, name="invoice_send_whatsapp"),

    # Liens publics (sans connexion), utilisés pour le partage WhatsApp.
    path("partage/factures/<uuid:invoice_id>/pdf/", views.invoice_public_pdf, name="invoice_public_pdf"),
    path("partage/factures/<uuid:invoice_id>/photos/", views.invoice_public_gallery, name="invoice_public_gallery"),
    path("partage/factures/<uuid:invoice_id>/", views.invoice_public_view, name="invoice_public_view"),
]
