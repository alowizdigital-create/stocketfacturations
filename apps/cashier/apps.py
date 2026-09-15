from django.apps import AppConfig


class CashierConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cashier"
    label = "cashier"
    verbose_name = "Caisse"
