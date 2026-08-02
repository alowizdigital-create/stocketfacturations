from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("comptes/", include("apps.accounts.urls")),
    path("entreprises/", include("apps.tenants.urls")),
    path("catalogue/", include("apps.catalog.urls")),
    path("stock/", include("apps.stock.urls")),
    path("", include("apps.sales.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
