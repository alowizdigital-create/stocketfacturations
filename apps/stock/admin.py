from django.contrib import admin

from .models import StockLevel, StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["created_at", "boutique", "product", "type", "quantity", "source"]
    list_filter = ["boutique", "type", "source"]
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockLevel)
class StockLevelAdmin(admin.ModelAdmin):
    list_display = ["boutique", "product", "quantity", "updated_at"]
    list_filter = ["boutique"]
