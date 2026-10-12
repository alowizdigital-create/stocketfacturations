"""Vitrine publique d'une boutique : catalogue, panier, commande. Accessible
sans connexion, uniquement en ligne (jamais sur le poste hors-ligne), et
seulement tant que la boutique l'a activée et que l'abonnement est valide."""

from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
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


def _context(request, boutique, **extra):
    lines = catalog.cart_lines(request, boutique)
    context = {
        "boutique": boutique, "slug": boutique.shop_slug,
        "cart_count": catalog.cart_totals(boutique, lines)["count"],
    }
    context.update(extra)
    return context


def shop_home(request, slug):
    boutique = _shop(slug)
    products = catalog.published_products(boutique)

    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(name__icontains=query)
    category_id = request.GET.get("categorie", "")
    if category_id:
        products = products.filter(category_id=category_id)

    # Page par ordre alphabétique, produits épuisés repoussés en fin de page.
    products = products.order_by("name")
    page = Paginator(products, PAGE_SIZE).get_page(request.GET.get("page"))
    page.object_list = sorted(catalog.attach_prices(page.object_list, boutique), key=lambda p: p.stock <= 0)

    categories = Category.objects.filter(
        compte=boutique.compte, products__is_online=True, products__is_active=True,
    ).distinct().order_by("name")
    return render(request, "storefront/home.html", _context(
        request, boutique, page=page, query=query, categories=categories, category_id=category_id,
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
    ))


@require_POST
def cart_add(request, slug, product_id):
    boutique = _shop(slug)
    product = get_object_or_404(catalog.published_products(boutique), pk=product_id)
    try:
        quantity = max(int(request.POST.get("quantity", 1)), 1)
    except ValueError:
        quantity = 1
    cart = catalog.get_cart(request, slug)
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
    return redirect("storefront:cart", slug=slug)


def cart_view(request, slug):
    boutique = _shop(slug)
    lines = catalog.cart_lines(request, boutique)
    return render(request, "storefront/cart.html", _context(
        request, boutique, lines=lines, totals=catalog.cart_totals(boutique, lines),
        has_stock_problem=any(not line["in_stock"] for line in lines),
    ))


def checkout(request, slug):
    boutique = _shop(slug)
    lines = catalog.cart_lines(request, boutique)
    if not lines:
        return redirect("storefront:cart", slug=slug)

    totals = catalog.cart_totals(boutique, lines)
    mobile_money = bool(boutique.payment_methods)
    error = ""
    if request.method == "POST":
        form = CheckoutForm(request.POST, mobile_money_available=mobile_money)
        now = timezone.now().timestamp()
        if now - request.session.get(LAST_ORDER_SESSION_KEY, 0) < ORDER_COOLDOWN_SECONDS:
            error = _("Une commande vient d'être envoyée. Patientez quelques secondes.")
        elif form.is_valid():
            try:
                commande = services.place_order(boutique=boutique, lines=lines, data=form.cleaned_data)
            except services.OutOfStock as exc:
                error = _("Stock insuffisant pour : %(names)s. Modifiez votre panier.") % {"names": ", ".join(exc.names)}
            else:
                request.session[LAST_ORDER_SESSION_KEY] = now
                catalog.clear_cart(request, slug)
                return redirect("storefront:confirmation", slug=slug, order_id=commande.id)
    else:
        form = CheckoutForm(mobile_money_available=mobile_money)

    return render(request, "storefront/checkout.html", _context(
        request, boutique, form=form, lines=lines, totals=totals, error=error,
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
