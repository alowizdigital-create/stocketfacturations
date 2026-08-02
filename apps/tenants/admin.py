from django.contrib import admin

from .models import BoutiqueAPIToken, Boutique, Compte, Membership


@admin.register(Compte)
class CompteAdmin(admin.ModelAdmin):
    list_display = ["name", "plan", "email", "created_at"]
    search_fields = ["name", "email"]


@admin.register(Boutique)
class BoutiqueAdmin(admin.ModelAdmin):
    list_display = ["name", "compte", "code", "devise", "is_active"]
    list_filter = ["compte", "is_active"]
    search_fields = ["name", "code"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "boutique", "role", "is_active"]
    list_filter = ["role", "is_active", "boutique"]


@admin.register(BoutiqueAPIToken)
class BoutiqueAPITokenAdmin(admin.ModelAdmin):
    list_display = ["boutique", "is_active", "created_at", "last_used_at"]
    readonly_fields = ["token_hash", "created_at", "last_used_at"]
