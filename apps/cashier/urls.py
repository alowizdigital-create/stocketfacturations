from django.urls import path

from . import views

app_name = "cashier"

urlpatterns = [
    path("", views.cash_session_current, name="current"),
    path("sessions/", views.cash_session_list, name="session_list"),
    path("sessions/ouvrir/", views.cash_session_open, name="session_open"),
    path("sessions/<uuid:session_id>/", views.cash_session_detail, name="session_detail"),
    path("sessions/<uuid:session_id>/fermer/", views.cash_session_close, name="session_close"),
    path("sessions/<uuid:session_id>/mouvement/", views.cash_movement_create, name="movement_create"),
]
