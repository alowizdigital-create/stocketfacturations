from django.urls import path

from . import views

app_name = "stock"

urlpatterns = [
    path("", views.stock_level_list, name="stock_level_list"),
    path("mouvements/", views.movement_history, name="movement_history"),
    path("mouvements/nouveau/", views.movement_create, name="movement_create"),
]
