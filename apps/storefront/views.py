"""Vitrine publique d'une boutique : catalogue, panier, commande. Accessible
sans connexion, uniquement en ligne (jamais sur le poste hors-ligne), et
seulement tant que la boutique l'a activée et que l'abonnement est valide."""

from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.catalog.models import Category
from apps.sales.models import Invoice
from apps.sales.whatsapp import normalize_phone
from apps.tenants.models import Boutique

from . import catalog, services
from .forms import CheckoutForm

PAGE_SIZE = 24
ORDER_COOLDOWN_SECONDS = 20
LAST_ORDER_SESSION_KEY = "shop_last_order_at"


def _shop(slug):
    """La boutique publique pour cette adresse, ou 404 : adresse inconnue,
    vitrine désactivée, boutique désactivée, abonnement expiré, ou poste
    hors-ligne (pas de vitrine publique sur le poste local)."""
    if settings.IS_OFFLINE:
        raise Http404
    boutique = Boutique.objects.select_related("compte").filter(
        shop_slug=slug, shop_enabled=True, is_active=True,
    ).first()
    if boutique is None:
        raise Http404
    subscription = getattr(boutique.compte, "subscription", None)
    if subscription is not None and not subscription.is_active:
        raise Http404
    return boutique


SORTS = {
    "az": ("name",), "za": ("-name",),
    "prix_asc": ("default_sale_price", "name"), "prix_desc": ("-default_sale_price", "name"),
    "recent": ("-created_at",),
}


def _contact_url(boutique):
    number = normalize_phone(boutique.phone, country_calling_code=boutique.country_calling_code)
    return f"https://wa.me/{number}" if number else ""


def _context(request, boutique, **extra):
    lines = catalog.cart_lines(request, boutique)
    context = {
        "boutique": boutique, "slug": boutique.shop_slug,
        "cart_count": catalog.cart_totals(boutique, lines)["count"],
        "contact_url": _contact_url(boutique),
        "delivery_banner": boutique.delivery_banner,
    }
    context.update(extra)
    return context


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _decimal(raw):
    try:
        return Decimal(raw) if raw not in (None, "") else None
    except Exception:  # noqa: BLE001 — saisie libre dans l'URL
        return None


def _sort_labels():
    return [
        ("az", _("Alphabétique, de A à Z")), ("za", _("Alphabétique, de Z à A")),
        ("prix_asc", _("Prix : faible à élevé")), ("prix_desc", _("Prix : élevé à faible")),
        ("recent", _("Plus récents")),
    ]


def _query_without(request, *keys):
    """Paramètres GET actuels sans `keys` (pour les liens de pagination)."""
    params = request.GET.copy()
    for key in keys:
        params.pop(key, None)
    return params.urlencode()


def shop_home(request, slug):
    boutique = _shop(slug)
    products = catalog.published_products(boutique)

    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(name__icontains=query)
    category_id = request.GET.get("categorie", "")
    if category_id:
        products = products.filter(category_id=category_id)

    available_only = request.GET.get("dispo") == "1"
    if available_only:
        products = products.filter(stock__gt=0)
    price_min, price_max = _decimal(request.GET.get("prix_min")), _decimal(request.GET.get("prix_max"))
    if price_min is not None:
        products = products.filter(default_sale_price__gte=price_min)
    if price_max is not None:
        products = products.filter(default_sale_price__lte=price_max)

    sort = request.GET.get("tri", "az")
    if sort not in SORTS:
        sort = "az"
    products = products.order_by(*SORTS[sort])
    page = Paginator(products, PAGE_SIZE).get_page(request.GET.get("page"))
    page.object_list = catalog.attach_prices(page.object_list, boutique)

    categories = Category.objects.filter(
        compte=boutique.compte, products__is_online=True, products__is_active=True,
    ).distinct().order_by("name")
    return render(request, "storefront/home.html", _context(
        request, boutique, page=page, query=query, categories=categories, category_id=category_id,
        available_only=available_only, price_min=request.GET.get("prix_min", ""),
        price_max=request.GET.get("prix_max", ""), sort=sort, sorts=_sort_labels(),
        qs_base=_query_without(request, "page", "tri"), qs_page=_query_without(request, "page"),
    ))


def product_detail(request, slug, product_id):
    boutique = _shop(slug)
    product = get_object_or_404(catalog.published_products(boutique), pk=product_id)
    product = catalog.attach_prices([product], boutique)[0]
    images = [product.image] if product.image else []
    images += [extra.image for extra in product.extra_images.all()]
    return render(request, "storefront/product.html", _context(
        request, boutique, product=product, images=images,
        max_qty=int(min(product.stock, catalog.MAX_QTY_PER_LINE)),
        related=catalog.related_products(boutique, product),
    ))


@require_POST
def cart_add(request, slug, product_id):
    boutique = _shop(slug)
    product = get_object_or_404(catalog.published_products(boutique), pk=product_id)
    try:
        quantity = max(int(request.POST.get("quantity", 1)), 1)
    except ValueError:
        quantity = 1
    buy_now = bool(request.POST.get("buy_now"))
    # « Commander maintenant » : commande directe de cet article seul.
    cart = {} if buy_now else catalog.get_cart(request, slug)
    wanted = cart.get(str(product.id), 0) + quantity
    allowed = int(min(product.stock, catalog.MAX_QTY_PER_LINE))
    if allowed <= 0:
        _flash(request, _("« %(name)s » est épuisé.") % {"name": product.name})
        return redirect("storefront:product", slug=slug, product_id=product.id)
    if wanted > allowed:
        wanted = allowed
        _flash(request, _("Stock limité : quantité ramenée à %(n)s.") % {"n": allowed})
    cart[str(product.id)] = wanted
    catalog.save_cart(request, slug, cart)
    if _is_ajax(request):
        return JsonResponse({"checkout": reverse("storefront:checkout", args=[slug]), "buy_now": buy_now})
    if buy_now:
        return redirect("storefront:checkout", slug=slug)
    return redirect("storefront:cart", slug=slug)


@require_POST
def cart_update(request, slug, product_id):
    _shop(slug)
    cart = catalog.get_cart(request, slug)
    key = str(product_id)
    if key in cart:
        try:
            quantity = int(request.POST.get("quantity", 0))
        except ValueError:
            quantity = cart[key]
        if quantity <= 0:
            del cart[key]
        else:
            cart[key] = min(quantity, catalog.MAX_QTY_PER_LINE)
        catalog.save_cart(request, slug, cart)
    if _is_ajax(request):
        return JsonResponse({"ok": True})
    return redirect("storefront:cart", slug=slug)


def cart_view(request, slug):
    boutique = _shop(slug)
    lines = catalog.cart_lines(request, boutique)
    # Panier latéral (JS) : seul le contenu est renvoyé.
    template = "storefront/_cart_drawer.html" if _is_ajax(request) or request.GET.get("drawer") else "storefront/cart.html"
    return render(request, template, _context(
        request, boutique, lines=lines, totals=catalog.cart_totals(boutique, lines),
        has_stock_problem=any(not line["in_stock"] for line in lines),
    ))


def checkout(request, slug):
    boutique = _shop(slug)
    lines = catalog.cart_lines(request, boutique)
    ajax = _is_ajax(request)
    if not lines:
        if ajax:
            return JsonResponse({"empty": True})
        return redirect("storefront:cart", slug=slug)

    zones = catalog.delivery_zones(boutique)
    mobile_money = bool(boutique.payment_methods)
    error = ""
    totals = catalog.cart_totals(boutique, lines)
    if request.method == "POST":
        form = CheckoutForm(request.POST, mobile_money_available=mobile_money, zones=zones)
        zone = next((z for z in zones if str(z.id) == request.POST.get("zone")), None)
        totals = catalog.cart_totals(boutique, lines, zone)
        now = timezone.now().timestamp()
        if now - request.session.get(LAST_ORDER_SESSION_KEY, 0) < ORDER_COOLDOWN_SECONDS:
            error = _("Une commande vient d'être envoyée. Patientez quelques secondes.")
        elif form.is_valid():
            try:
                commande = services.place_order(boutique=boutique, lines=lines, data=form.cleaned_data, zone=zone)
            except services.OutOfStock as exc:
                error = _("Stock insuffisant pour : %(names)s. Modifiez votre panier.") % {"names": ", ".join(exc.names)}
            else:
                request.session[LAST_ORDER_SESSION_KEY] = now
                catalog.clear_cart(request, slug)
                target = reverse("storefront:confirmation", args=[slug, commande.id])
                if ajax:
                    return JsonResponse({"redirect": target})
                return redirect(target)
    else:
        form = CheckoutForm(mobile_money_available=mobile_money, zones=zones)

    # Fenêtre de commande (JS) : seul le formulaire est renvoyé, pas la page.
    template = "storefront/_checkout_form.html" if ajax or request.GET.get("modal") else "storefront/checkout.html"
    return render(request, template, _context(
        request, boutique, form=form, lines=lines, totals=totals, error=error, zones=zones,
        payment_methods=boutique.payment_methods,
    ))


def confirmation(request, slug, order_id):
    boutique = _shop(slug)
    commande = get_object_or_404(
        Invoice.objects.select_related("client").prefetch_related("lines"),
        pk=order_id, boutique=boutique, source=Invoice.EN_LIGNE,
    )
    whatsapp = None
    number = normalize_phone(boutique.phone, country_calling_code=boutique.country_calling_code)
    if number:
        text = _("Bonjour, je viens de passer la commande n°%(n)s sur votre boutique en ligne.") % {"n": commande.commande_seq}
        whatsapp = f"https://wa.me/{number}?text={quote(text)}"
    return render(request, "storefront/confirmation.html", _context(
        request, boutique, commande=commande, whatsapp=whatsapp, payment_methods=boutique.payment_methods,
    ))


def _flash(request, message):
    from django.contrib import messages

    messages.info(request, message)
