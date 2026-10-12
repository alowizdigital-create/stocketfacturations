from django.urls import path

from . import views

app_name = "storefront"

urlpatterns = [
    path("<slug:slug>/", views.shop_home, name="home"),
    path("<slug:slug>/produit/<uuid:product_id>/", views.product_detail, name="product"),
    path("<slug:slug>/panier/", views.cart_view, name="cart"),
    path("<slug:slug>/panier/ajouter/<uuid:product_id>/", views.cart_add, name="cart_add"),
    path("<slug:slug>/panier/modifier/<uuid:product_id>/", views.cart_update, name="cart_update"),
    path("<slug:slug>/commander/", views.checkout, name="checkout"),
    path("<slug:slug>/commande/<uuid:order_id>/", views.confirmation, name="confirmation"),
]
