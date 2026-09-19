from django.urls import path

from . import views

app_name = "cashier"

urlpatterns = [
    # "Caisse de la boutique" (session partagée, CashSession) retirée de
    # l'interface à la demande de l'utilisateur — remplacée dans l'usage
    # par la caisse individuelle (perso/) + la vue d'ensemble admin
    # (entreprise/). Le modèle, les vues et la synchro offline restent en
    # place (apps.sync s'appuie dessus, voir apps/sync/serializers.py et
    # views.py) : seules ces routes disparaissent, aucune donnée n'est
    # perdue et le module peut être réactivé en les décommentant.
    # path("", views.cash_session_current, name="current"),
    # path("sessions/", views.cash_session_list, name="session_list"),
    # path("sessions/ouvrir/", views.cash_session_open, name="session_open"),
    # path("sessions/<uuid:session_id>/", views.cash_session_detail, name="session_detail"),
    # path("sessions/<uuid:session_id>/fermer/", views.cash_session_close, name="session_close"),
    # path("sessions/<uuid:session_id>/mouvement/", views.cash_movement_create, name="movement_create"),
    path("perso/", views.my_cash, name="my_cash"),
    path("perso/transfert/", views.cash_transfer_create, name="transfer_create"),
    path("perso/transfert/<uuid:transfer_id>/accepter/", views.cash_transfer_accept, name="transfer_accept"),
    path("perso/transfert/<uuid:transfer_id>/refuser/", views.cash_transfer_reject, name="transfer_reject"),
    path("entreprise/", views.entreprise_cash_overview, name="entreprise_overview"),
]
