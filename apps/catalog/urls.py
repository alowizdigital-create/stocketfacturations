from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("produits/", views.product_list, name="product_list"),
    path("produits/rechercher/", views.product_search, name="product_search"),
    path("produits/nouveau/", views.product_create, name="product_create"),
    path("produits/<uuid:product_id>/modifier/", views.product_update, name="product_update"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/nouvelle/", views.category_create, name="category_create"),
    path("categories/<uuid:category_id>/modifier/", views.category_update, name="category_update"),
    path("categories/<uuid:category_id>/supprimer/", views.category_delete, name="category_delete"),
    path("unites/", views.unit_list, name="unit_list"),
    path("unites/nouvelle/", views.unit_create, name="unit_create"),
    path("unites/<uuid:unit_id>/modifier/", views.unit_update, name="unit_update"),
    path("unites/<uuid:unit_id>/supprimer/", views.unit_delete, name="unit_delete"),
]
