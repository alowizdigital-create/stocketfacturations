from django.urls import path

from . import views

app_name = "sync"

urlpatterns = [
    path("ping/", views.PingView.as_view(), name="ping"),
    path("activate/", views.activate_device, name="activate"),

    path("push/clients/", views.PushClientsView.as_view(), name="push_clients"),
    path("push/stock-movements/", views.PushStockMovementsView.as_view(), name="push_stock_movements"),
    path("push/invoices/", views.PushInvoicesView.as_view(), name="push_invoices"),
    path("push/payments/", views.PushPaymentsView.as_view(), name="push_payments"),
    path("push/sale-transactions/", views.PushSaleTransactionsView.as_view(), name="push_sale_transactions"),

    path("pull/tenants/boutique/", views.PullBoutiqueView.as_view(), name="pull_boutique"),
    path("pull/accounts/users/", views.PullUsersView.as_view(), name="pull_users"),
    path("pull/tenants/memberships/", views.PullMembershipsView.as_view(), name="pull_memberships"),
    path("pull/tenants/exchange-rates/", views.PullExchangeRatesView.as_view(), name="pull_exchange_rates"),
    path("pull/sales/clients/", views.PullClientsView.as_view(), name="pull_clients"),
    path("pull/sales/tax-rates/", views.PullTaxRatesView.as_view(), name="pull_tax_rates"),
    path("pull/catalog/categories/", views.PullCategoriesView.as_view(), name="pull_categories"),
    path("pull/catalog/units/", views.PullUnitsView.as_view(), name="pull_units"),
    path("pull/catalog/products/", views.PullProductsView.as_view(), name="pull_products"),
    path(
        "pull/catalog/product-boutique-prices/",
        views.PullProductBoutiquePricesView.as_view(),
        name="pull_product_boutique_prices",
    ),
    path("pull/stock/levels/", views.PullStockLevelsView.as_view(), name="pull_stock_levels"),
]
