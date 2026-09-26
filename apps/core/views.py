from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.catalog.models import EXPIRY_ALERT_DAYS
from apps.catalog.services import (
    expired_products_in_stock, expiring_soon_products_in_stock, get_effective_low_stock_threshold,
)
from apps.sales.models import Invoice, Sale
from apps.sales.reports import margin_report
from apps.stock.models import StockLevel
from apps.tenants.models import Membership

from .models import Notification, ShortLink


def service_worker(request):
    """Sert le service worker depuis la racine du site (/sw.js) plutôt que
    /static/sw.js : la portée par défaut d'un service worker est le
    dossier de son URL, il doit donc être servi hors de /static/ pour
    pouvoir contrôler toute l'application, pas seulement les fichiers
    statiques."""
    path = settings.BASE_DIR / "static" / "sw.js"
    return HttpResponse(path.read_text(encoding="utf-8"), content_type="application/javascript")


def short_link_redirect(request, code):
    """Public (pas de connexion requise) — c'est un lien cliqué par un
    client, jamais par un membre du personnel connecté."""
    link = get_object_or_404(ShortLink, code=code)
    return redirect(link.target_path)


DOWNLOAD_FILENAME = "Zweey-Windows.zip"


def landing(request):
    """Page d'accueil publique de Zweey (visiteur non connecté, version en
    ligne). Le poste offline n'en a pas : il est déjà installé et lié à une
    boutique, on l'envoie directement à la connexion.

    Le bouton de téléchargement du poste offline ne s'affiche que si le
    fichier existe réellement dans media/ (voir DEPLOY.md pour comment il y
    arrive) — sinon on préfère ne rien montrer plutôt qu'un lien mort."""
    download_path = settings.MEDIA_ROOT / "downloads" / DOWNLOAD_FILENAME
    download_url = settings.MEDIA_URL + "downloads/" + DOWNLOAD_FILENAME if download_path.exists() else None
    return render(request, "core/landing.html", {"download_url": download_url})


def home(request):
    if not request.user.is_authenticated:
        if settings.IS_OFFLINE:
            return redirect_to_login(request.get_full_path())
        return landing(request)

    if request.boutique is None:
        # Un super-administrateur de la plateforme n'a souvent aucune
        # boutique : son point d'entrée est l'administration plateforme.
        if request.user.is_superuser and not settings.IS_OFFLINE:
            return redirect("platform_admin:dashboard")
        return redirect("tenants:choose_boutique")

    boutique = request.boutique
    today = timezone.localdate()
    month_start = today.replace(day=1)

    # Le chiffre d'affaires reflète les ventes confirmées, pas seulement
    # celles pour lesquelles une facture a été générée — beaucoup de
    # ventes n'en ont jamais besoin.
    ca_mois = (
        Sale.objects.filter(
            boutique=boutique,
            status=Sale.CONFIRMEE,
            sale_date__gte=month_start,
        ).aggregate(total=Sum("total_ttc"))["total"]
        or 0
    )

    ca_jour = (
        Sale.objects.filter(
            boutique=boutique,
            status=Sale.CONFIRMEE,
            sale_date=today,
        ).aggregate(total=Sum("total_ttc"))["total"]
        or 0
    )

    nb_factures_impayees = Invoice.objects.filter(
        boutique=boutique,
        type=Invoice.FACTURE,
        status__in=[Invoice.VALIDEE, Invoice.PARTIELLEMENT_PAYEE],
    ).count()

    # Commandes en attente ou en préparation, pas encore remises au client
    # (voir apps.sales.services.mark_commande_delivered) — les deux
    # premiers niveaux de Invoice.DELIVERY_STATUS_CHOICES. Une commande
    # annulée ne compte jamais comme "à livrer".
    nb_commandes_non_livrees = Invoice.objects.filter(
        boutique=boutique,
        type=Invoice.COMMANDE,
    ).exclude(delivery_status=Invoice.LIVREE).exclude(status=Invoice.ANNULEE).count()

    levels = StockLevel.objects.filter(boutique=boutique).select_related("product")
    nb_stock_bas = sum(
        1
        for level in levels
        if level.quantity <= get_effective_low_stock_threshold(level.product, boutique)
    )

    nb_produits_perimes = expired_products_in_stock(request.compte, boutique).count()
    nb_produits_bientot_perimes = expiring_soon_products_in_stock(request.compte, boutique).count()

    dernieres_ventes = (
        Sale.objects.filter(boutique=boutique).select_related("client").order_by("-created_at")[:5]
    )

    # Bénéfice du mois : révèle les prix d'achat, donc pas pour un caissier.
    margin_mois = margin_mois_percent = None
    if request.boutique_role in (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE):
        totals = margin_report(boutique, month_start, today)["totals"]
        if totals["revenue_known"] > 0 or totals["expenses_total"] > 0:
            # Bénéfice net : marge sur les ventes moins les dépenses du mois.
            margin_mois = totals["net_profit"]
            margin_mois_percent = totals["margin_percent"] if not totals["expenses_total"] else None

    context = {
        "margin_mois": margin_mois,
        "margin_mois_percent": margin_mois_percent,
        "nb_produits_perimes": nb_produits_perimes,
        "nb_produits_bientot_perimes": nb_produits_bientot_perimes,
        "expiry_alert_days": EXPIRY_ALERT_DAYS,
        "ca_mois": ca_mois,
        "ca_jour": ca_jour,
        "nb_factures_impayees": nb_factures_impayees,
        "nb_commandes_non_livrees": nb_commandes_non_livrees,
        "nb_stock_bas": nb_stock_bas,
        "dernieres_ventes": dernieres_ventes,
    }
    return render(request, "core/home.html", context)


@login_required
def notification_open(request, notification_id):
    """Clic sur une notification (voir la cloche dans templates/base.html) :
    marque lue puis redirige vers sa cible (ex: sa caisse pour accepter un
    transfert) — voir apps.core.notifications.notify. Repli sur l'accueil
    si aucune cible n'a été renseignée."""
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])
    return redirect(notification.url or "core:home")
