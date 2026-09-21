from django.contrib import admin

from .models import Expense, Purchase, PurchaseLine, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "compte", "phone", "is_active"]
    search_fields = ["name", "phone", "email"]
    list_filter = ["is_active"]


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0
    readonly_fields = ["product", "quantity", "unit_cost", "line_total", "cost_before", "cost_after", "movement"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ["number", "boutique", "supplier", "received_date", "total_cost"]
    search_fields = ["number", "reference", "supplier__name"]
    inlines = [PurchaseLineInline]


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["label", "category", "amount", "expense_date", "boutique", "cancelled_at"]
    list_filter = ["category"]
    search_fields = ["label"]
