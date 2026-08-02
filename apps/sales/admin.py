from django.contrib import admin

from .models import Client, Invoice, InvoiceLine, Payment, Sale, SaleLine, TaxRate


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


class SaleLineInline(admin.TabularInline):
    model = SaleLine
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "boutique", "phone", "email"]
    list_filter = ["boutique"]
    search_fields = ["name", "phone", "email"]


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ["name", "compte", "rate", "is_default", "active_from"]
    list_filter = ["compte"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "boutique", "type", "status", "issue_date", "total_ttc"]
    list_filter = ["boutique", "type", "status"]
    search_fields = ["number"]
    inlines = [InvoiceLineInline, PaymentInline]


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["number", "boutique", "status", "sale_date", "total_ttc", "invoice"]
    list_filter = ["boutique", "status"]
    search_fields = ["number"]
    inlines = [SaleLineInline]
