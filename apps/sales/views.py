import csv
import io
import json
import quopri
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_POST

from apps.catalog.models import Product
from apps.core.models import ShortLink
from apps.core.permissions import boutique_role_required
from apps.sync import outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from . import reminders, reports, services
from .forms import (
    ClientForm, ClientImportForm, InvoiceForm, InvoiceLineFormSet, InvoicePaymentForm, PaymentForm, SaleForm,
)
from .models import Client, Invoice, Payment, PaymentReminder, Sale
from .pdf import render_invoice_pdf
from .whatsapp import build_link_for_message, build_message, build_share_link, normalize_phone
from .whatsapp_api import WhatsAppSendError, is_configured, send_document

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE, Membership.CAISSIER)


@login_required
def client_list(request):
    query = request.GET.get("q", "").strip()
    clients = Client.objects.filter(boutique=request.boutique)
    if query:
        clients = clients.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query)
        )
    clients = clients.order_by("-created_at")
    if not query:
        clients = clients[:10]
    return render(request, "sales/client_list.html", {"clients": clients, "query": query})


@login_required
def client_search(request):
    """Recherche client en direct pour l'écran de vente (POS), sur le même
    principe que catalog:product_search."""
    query = request.GET.get("q", "").strip()
    clients = Client.objects.filter(boutique=request.boutique)
    if query:
        clients = clients.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    clients = clients.order_by("name")[:20]

    results = [
        {"id": str(c.id), "name": c.name, "phone": c.phone}
        for c in clients
    ]
    return JsonResponse({"results": results})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def client_create(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.boutique = request.boutique
            client.save()
            if settings.IS_OFFLINE:
                outbox.enqueue(OutboxEntry.CLIENT, client.id)
            messages.success(request, _("Client créé."))
            return redirect("sales:client_list")
    else:
        form = ClientForm()
    return render(request, "sales/client_form.html", {"form": form})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def client_quick_create(request):
    """Création rapide en JSON, utilisée par la pop-up des formulaires de
    vente/devis/commande (voir sale_form.html et invoice_form.html) —
    évite de quitter la page pour ajouter un client manquant. Même
    logique de sauvegarde que client_create, juste une réponse JSON au
    lieu d'une redirection (voir apps.catalog.views.category_quick_create
    pour le même patron)."""
    if request.method != "POST":
        return JsonResponse({"detail": _("Méthode non autorisée.")}, status=405)
    form = ClientForm(request.POST)
    if form.is_valid():
        client = form.save(commit=False)
        client.boutique = request.boutique
        client.save()
        if settings.IS_OFFLINE:
            outbox.enqueue(OutboxEntry.CLIENT, client.id)
        return JsonResponse({"id": str(client.id), "name": client.name, "phone": client.phone})
    return JsonResponse({"errors": form.errors}, status=400)


CLIENT_IMPORT_HEADER_ALIASES = {
    "name": "name", "nom": "name", "nom complet": "name", "client": "name",
    "phone": "phone", "telephone": "phone", "téléphone": "phone", "tel": "phone",
    "téléphone portable": "phone", "phone number": "phone", "mobile": "phone",
    "email": "email", "e-mail": "email", "mail": "email", "courriel": "email",
    "address": "address", "adresse": "address",
}


def _parse_csv_contacts(text):
    """Renvoie une liste de dicts {name, phone, email, address} à partir
    d'un CSV. Seul le nom est obligatoire — téléphone/email/adresse sont
    de simples chaînes vides si la colonne est absente ou la cellule vide."""

    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    if not reader.fieldnames:
        return None, _("Fichier CSV vide ou illisible.")

    field_map = {}
    for raw_header in reader.fieldnames:
        key = CLIENT_IMPORT_HEADER_ALIASES.get(raw_header.strip().lower())
        if key:
            field_map[raw_header] = key

    if "name" not in field_map.values():
        return None, _(
            "Aucune colonne « nom » reconnue dans le fichier CSV. "
            "Colonnes attendues : nom, téléphone, email, adresse."
        )

    contacts = []
    for row in reader:
        data = {"name": "", "phone": "", "email": "", "address": ""}
        for raw_header, key in field_map.items():
            data[key] = (row.get(raw_header) or "").strip()
        contacts.append(data)
    return contacts, None


def _unfold_vcard_lines(text):
    """Les lignes vCard peuvent être repliées sur plusieurs lignes physiques
    (RFC 6350) : toute ligne commençant par une espace/tabulation est la
    continuation de la précédente."""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _decode_vcard_value(raw_value, params):
    value = raw_value
    if params.get("ENCODING") == "QUOTED-PRINTABLE":
        try:
            charset = params.get("CHARSET", "utf-8")
            value = quopri.decodestring(value.encode("ascii", errors="ignore")).decode(charset, errors="replace")
        except (ValueError, LookupError):
            pass
    return (
        value.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )


def _parse_vcard_property_line(line):
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    parts = head.split(";")
    prop = parts[0].strip().upper()
    if "." in prop:  # groupe préfixé, ex "item1.TEL"
        prop = prop.split(".")[-1]
    params = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.strip().upper()] = val.strip().upper()
    return prop, params, value


def _parse_vcf_contacts(text):
    """Renvoie une liste de dicts {name, phone, email, address} à partir
    d'un fichier vCard (.vcf) — export standard des carnets de contacts
    téléphone/iCloud/Google/Outlook. Seul le nom (FN ou N) est requis."""

    contacts = []
    current = None
    for raw_line in _unfold_vcard_lines(text):
        line = raw_line.strip("﻿").strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            current = {"fn": "", "n": "", "phone": "", "email": "", "address": ""}
            continue
        if upper.startswith("END:VCARD"):
            if current is not None:
                name = (current["fn"] or current["n"]).strip()
                if name:
                    contacts.append({
                        "name": name,
                        "phone": current["phone"],
                        "email": current["email"],
                        "address": current["address"],
                    })
            current = None
            continue
        if current is None:
            continue

        parsed = _parse_vcard_property_line(line)
        if not parsed:
            continue
        prop, params, raw_value = parsed
        value = _decode_vcard_value(raw_value, params).strip()

        if prop == "FN" and not current["fn"]:
            current["fn"] = value
        elif prop == "N" and not current["n"]:
            components = value.split(";")
            family = components[0] if len(components) > 0 else ""
            given = components[1] if len(components) > 1 else ""
            current["n"] = " ".join(p for p in [given, family] if p)
        elif prop == "TEL" and not current["phone"]:
            current["phone"] = value
        elif prop == "EMAIL" and not current["email"]:
            current["email"] = value
        elif prop == "ADR" and not current["address"]:
            components = [c.strip() for c in value.split(";") if c.strip()]
            current["address"] = ", ".join(components)

    if not contacts:
        return None, _("Aucun contact trouvé dans le fichier vCard.")
    return contacts, None


@login_required
@boutique_role_required(*MANAGE_ROLES)
def client_import(request):
    """Import en masse de contacts clients depuis un fichier CSV ou vCard
    (.vcf, export d'un téléphone/carnet de contacts). Seul le nom est
    obligatoire — téléphone, email et adresse restent optionnels. Les
    doublons (même nom + même téléphone déjà présents dans la boutique)
    sont ignorés plutôt que dupliqués."""

    if request.method == "POST":
        form = ClientImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["file"]
            raw_bytes = uploaded.read()
            try:
                text = raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = raw_bytes.decode("latin-1")
                except UnicodeDecodeError:
                    messages.error(request, _("Le fichier doit être encodé en UTF-8."))
                    return render(request, "sales/client_import.html", {"form": form})

            filename = (uploaded.name or "").lower()
            is_vcf = filename.endswith(".vcf") or "BEGIN:VCARD" in text[:200].upper()

            if is_vcf:
                contacts, error = _parse_vcf_contacts(text)
            else:
                contacts, error = _parse_csv_contacts(text)

            if error:
                messages.error(request, error)
                return render(request, "sales/client_import.html", {"form": form})

            existing = set(
                Client.objects.filter(boutique=request.boutique).values_list("name", "phone")
            )
            to_create = []
            seen = set()
            skipped = 0
            for data in contacts:
                if not data["name"]:
                    skipped += 1
                    continue

                dedupe_key = (data["name"], data["phone"])
                if dedupe_key in existing or dedupe_key in seen:
                    skipped += 1
                    continue
                seen.add(dedupe_key)

                to_create.append(
                    Client(
                        boutique=request.boutique,
                        name=data["name"],
                        phone=data["phone"],
                        email=data["email"],
                        address=data["address"],
                    )
                )

            Client.objects.bulk_create(to_create)

            if to_create:
                text = _("%(count)s client(s) importé(s).") % {"count": len(to_create)}
                if skipped:
                    text += " " + _("%(count)s ligne(s) ignorée(s) (doublon ou nom manquant).") % {"count": skipped}
                messages.success(request, text)
            else:
                messages.warning(request, _("Aucun nouveau client importé (doublons ou fichier vide)."))
            return redirect("sales:client_list")
    else:
        form = ClientImportForm()
    return render(request, "sales/client_import.html", {"form": form})


def _extract_lines_data(formset, require_changed=True):
    """`require_changed=True` (comportement historique, création) : ignore
    les lignes jamais touchées par l'utilisateur — filtre les lignes vides
    du formset. Sur un formulaire d'édition pré-rempli (voir devis_update),
    ce filtre supprimerait à tort une ligne existante resoumise sans
    modification : `form.has_changed()` la considère alors "inchangée"
    puisqu'elle correspond exactement à son `initial` — d'où ce paramètre."""
    lines_data = []
    for form in formset:
        if form.cleaned_data.get("DELETE"):
            continue
        if require_changed and not form.has_changed():
            continue
        description = form.cleaned_data.get("description")
        if not description:
            continue
        lines_data.append(
            {
                "product": form.cleaned_data.get("product"),
                "description": description,
                "quantity": form.cleaned_data["quantity"],
                "unit_price_ht": form.cleaned_data["unit_price_ht"],
                "tva_rate": form.cleaned_data["tva_rate"],
                "discount_amount": Decimal(form.cleaned_data.get("discount_amount") or 0),
            }
        )
    return lines_data


# --- Ventes -----------------------------------------------------------
# La vente est le point d'entrée du module : c'est elle qui déduit le
# stock à sa confirmation. La facture n'est qu'un document généré ensuite
# à partir d'une vente confirmée — voir sale_generate_invoice ci-dessous.

@login_required
def sale_list(request):
    query = request.GET.get("q", "").strip()
    # period=today/month : liens directs depuis les cartes de chiffre
    # d'affaires du tableau de bord (apps.core.views.home) — mêmes
    # critères (status=CONFIRMEE, plage de sale_date) que les totaux
    # affichés, pour que le montant et la liste correspondent exactement.
    period = request.GET.get("period") or ""
    date_from_str = request.GET.get("date_from") or ""
    date_to_str = request.GET.get("date_to") or ""
    date_from = parse_date(date_from_str) if date_from_str else None
    date_to = parse_date(date_to_str) if date_to_str else None
    custom_range = bool(date_from or date_to)

    sales = Sale.objects.filter(boutique=request.boutique).select_related("client", "invoice")

    revenue_total = None
    if period in ("today", "month") or custom_range:
        # Le chiffre d'affaires ne compte que les ventes confirmées (même
        # définition que apps.core.views.home::ca_mois/ca_jour) — la liste
        # affichée dans ce cas se limite donc aussi aux ventes confirmées,
        # pour que le total affiché corresponde exactement à ce qui est listé.
        sales = sales.filter(status=Sale.CONFIRMEE)
        if custom_range:
            if date_from:
                sales = sales.filter(sale_date__gte=date_from)
            if date_to:
                sales = sales.filter(sale_date__lte=date_to)
        else:
            today = timezone.localdate()
            if period == "today":
                sales = sales.filter(sale_date=today)
            else:
                sales = sales.filter(sale_date__gte=today.replace(day=1))
        revenue_total = sales.aggregate(total=Sum("total_ttc"))["total"] or Decimal("0")

    if query:
        sales = sales.filter(Q(number__icontains=query) | Q(client__name__icontains=query))
    if not query and not period and not custom_range:
        sales = sales[:10]
    return render(
        request, "sales/sale_list.html",
        {
            "sales": sales, "query": query, "period": period,
            "date_from": date_from_str, "date_to": date_to_str,
            "revenue_total": revenue_total,
        },
    )


def _parse_cart(request):
    """Décode le panier envoyé par l'écran de caisse (JSON construit côté
    JS). Le produit et sa TVA sont toujours relus en base — jamais fait
    confiance au JSON pour ces valeurs — seule la quantité et le prix
    unitaire (modifiable par le caissier) viennent du client."""

    from apps.stock.models import StockLevel

    raw = request.POST.get("cart_json", "")
    try:
        items = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return None, _("Panier invalide.")

    if not items:
        return None, _("Ajoutez au moins un produit au panier.")

    product_ids = [item.get("product_id") for item in items]
    products = Product.objects.filter(id__in=product_ids, compte=request.compte, is_active=True)
    products_by_id = {str(p.id): p for p in products}
    stock_by_product = {
        level.product_id: level.quantity
        for level in StockLevel.objects.filter(boutique=request.boutique, product__in=products)
    }

    lines_data = []
    requested_by_product = {}
    for item in items:
        product = products_by_id.get(str(item.get("product_id")))
        if product is None:
            return None, _("Un produit du panier n'existe plus.")
        try:
            quantity = Decimal(str(item.get("quantity", "0")))
            unit_price_ht = Decimal(str(item.get("unit_price_ht", "0")))
        except InvalidOperation:
            return None, _("Quantité ou prix invalide dans le panier.")
        if quantity <= 0 or unit_price_ht < 0 or quantity != quantity.to_integral_value():
            return None, _("Quantité ou prix invalide dans le panier.")

        # Cumulé au cas où le même produit apparaîtrait sur plusieurs lignes
        # du panier — la quantité totale demandée ne doit jamais dépasser le
        # stock réellement disponible dans cette boutique.
        requested_by_product[product.id] = requested_by_product.get(product.id, Decimal("0")) + quantity
        available = stock_by_product.get(product.id, Decimal("0"))
        if requested_by_product[product.id] > available:
            return None, _(
                "Stock insuffisant pour « %(name)s » : %(available)s disponible(s), "
                "%(requested)s demandé(s)."
            ) % {
                "name": product.name,
                "available": available,
                "requested": requested_by_product[product.id],
            }

        lines_data.append(
            {
                "product": product,
                "description": product.name,
                "quantity": quantity,
                "unit_price_ht": unit_price_ht,
                "tva_rate": product.tva_rate,
            }
        )
    return lines_data, None


@login_required
@boutique_role_required(*MANAGE_ROLES)
def sale_create(request):
    if request.method == "POST":
        form = SaleForm(request.POST, boutique=request.boutique)
        lines_data, error = _parse_cart(request)
        if error:
            messages.error(request, error)
        elif form.is_valid():
            sale = services.build_sale(
                boutique=request.boutique,
                client=form.cleaned_data["client"],
                created_by=request.user,
                lines_data=lines_data,
                currency=form.cleaned_data["currency"] or request.boutique.devise,
            )
            # La vente devient immédiatement réelle (stock déduit) et sa
            # facture est générée directement : le caissier choisit au
            # moment d'enregistrer si le client paie tout ou verse un
            # acompte (pop-up JS), il n'y a pas d'étape manuelle
            # "confirmer" / "facturer" séparée dans ce flux.
            services.confirm_sale(sale, created_by=request.user)
            invoice = services.generate_invoice_from_sale(sale, created_by=request.user)

            if form.cleaned_data["payment_type"] == "partial":
                paid_amount = min(form.cleaned_data["deposit_amount"], invoice.total_ttc)
            else:
                paid_amount = invoice.total_ttc

            if paid_amount > 0:
                services.record_payment(
                    invoice, amount=paid_amount, method=Payment.ESPECES, created_by=request.user,
                )

            if settings.IS_OFFLINE:
                # Une seule entrée pour toute la transaction (vente +
                # facture + paiement) — pas une par étape — pour préserver
                # l'atomicité du bundle côté serveur (push/sale-transactions/).
                outbox.enqueue(OutboxEntry.SALE_TRANSACTION, sale.id)

            messages.success(
                request,
                _("%(sale)s enregistrée et %(invoice)s générée.")
                % {"sale": sale.number, "invoice": invoice.number},
            )
            return redirect("sales:invoice_detail", invoice_id=invoice.id)
    else:
        form = SaleForm(boutique=request.boutique)

    return render(
        request,
        "sales/sale_form.html",
        {"form": form, "rate_map": request.boutique.exchange_rate_map},
    )


@login_required
def sale_detail(request, sale_id):
    sale = get_object_or_404(
        Sale.objects.select_related("client", "invoice").prefetch_related("lines"),
        id=sale_id,
        boutique=request.boutique,
    )
    return render(request, "sales/sale_detail.html", {"sale": sale})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def sale_confirm(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id, boutique=request.boutique)
    services.confirm_sale(sale, created_by=request.user)
    messages.success(request, _("%(sale)s confirmée, stock mis à jour.") % {"sale": sale.number})
    return redirect("sales:sale_detail", sale_id=sale.id)



@login_required
@boutique_role_required(*MANAGE_ROLES)
def sale_generate_invoice(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id, boutique=request.boutique)
    try:
        invoice = services.generate_invoice_from_sale(sale, created_by=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("sales:sale_detail", sale_id=sale.id)
    messages.success(request, _("Facture %(invoice)s générée.") % {"invoice": invoice.number})
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


# --- Devis & factures ---------------------------------------------------
# Un devis peut être créé directement (ce n'est pas une vente réelle : pas
# d'impact sur le stock). Une facture, elle, n'est plus créée à la main :
# elle est toujours générée à partir d'une vente confirmée (voir
# sale_generate_invoice).

@login_required
def invoice_list(request):
    """Liste des factures uniquement — les devis ont leur propre liste
    (voir devis_list), ce sont deux documents distincts pour l'utilisateur
    même s'ils partagent le même modèle Invoice en base."""
    query = request.GET.get("q", "").strip()
    # unpaid=1 : lien direct depuis la carte "Factures non soldées" du
    # tableau de bord (apps.core.views.home) — mêmes statuts que le
    # comptage nb_factures_impayees, pour que le chiffre et la liste
    # correspondent exactement.
    unpaid_only = request.GET.get("unpaid") == "1"
    invoices = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.FACTURE)
        .select_related("client")
    )
    if unpaid_only:
        invoices = invoices.filter(status__in=[Invoice.VALIDEE, Invoice.PARTIELLEMENT_PAYEE]).annotate(
            due=F("total_ttc") - Coalesce(Sum("payments__amount"), Value(Decimal("0")))
        )
    if query:
        invoices = invoices.filter(Q(number__icontains=query) | Q(client__name__icontains=query))
    if not query and not unpaid_only:
        invoices = invoices[:10]
    return render(
        request, "sales/invoice_list.html",
        {"invoices": invoices, "query": query, "unpaid_only": unpaid_only},
    )


@login_required
def devis_list(request):
    query = request.GET.get("q", "").strip()
    devis = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.DEVIS)
        .select_related("client")
    )
    if query:
        devis = devis.filter(Q(number__icontains=query) | Q(client__name__icontains=query))
    if not query:
        devis = devis[:10]
    return render(request, "sales/devis_list.html", {"devis": devis, "query": query})


def _commande_query(query):
    """Recherche d'une commande : par son numéro simple (« 12 »), par son
    numéro de document long ou par le nom du client."""
    # Des chiffres seuls désignent le numéro simple : ne pas les chercher dans
    # le numéro long, dont la date (20260921) ferait ressortir presque tout.
    if query.isdigit() and len(query) < 9:
        return Q(commande_seq=int(query)) | Q(client__name__icontains=query)
    return Q(number__icontains=query) | Q(client__name__icontains=query)


# Onglet affiché à l'ouverture de la liste des commandes — partagé par la
# page (commande_list) et la recherche en direct (commande_search), qui
# doivent toujours filtrer sur le même onglet.
DEFAULT_COMMANDE_TAB = Invoice.EN_COURS


def _deliver_and_validate(request, commande):
    """Livraison d'une commande = validation (voir services.deliver_commande),
    avec les messages à l'utilisateur. Renvoie True si la commande a été
    livrée. Utilisé par le bouton « Livrée » de la liste (commande_advance)
    ET par celui de la fiche (commande_mark_delivered), pour qu'ils
    agissent exactement pareil."""
    if settings.IS_OFFLINE and commande.status != Invoice.CONVERTIE:
        # Même limite que commande_generate_invoice : la conversion en
        # facture (stock, vente, CONVERTIE) n'est pas rejouable hors-ligne.
        messages.error(
            request,
            _(
                "La livraison valide la commande (facture, stock), ce qui n'est pas encore "
                "disponible hors-ligne — utilisez le poste en ligne."
            ),
        )
        return False
    try:
        invoice = services.deliver_commande(commande, delivered_by=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return False

    if invoice is None:
        messages.success(request, _("%(commande)s marquée comme livrée.") % {"commande": commande.number})
        return True
    left = invoice.balance_due
    if left > 0:
        messages.success(
            request,
            _("%(commande)s livrée et validée — facture %(invoice)s générée, reste à payer : %(left)s %(currency)s.") % {
                "commande": commande.number, "invoice": invoice.number, "left": left, "currency": invoice.currency,
            },
        )
    else:
        messages.success(
            request,
            _("%(commande)s livrée et validée — facture %(invoice)s générée et soldée.") % {
                "commande": commande.number, "invoice": invoice.number,
            },
        )
    return True


def _commande_next_action(commande):
    """Étape suivante d'une commande dans la colonne « Action » de la liste
    (voir commande_advance) : en attente -> en cours -> livrée. None quand
    il n'y a plus rien à avancer. `current` est l'état que l'utilisateur a
    sous les yeux — la vue vérifie qu'il n'a pas changé entre-temps."""
    if commande.delivery_status == Invoice.EN_ATTENTE:
        return {
            "current": Invoice.EN_ATTENTE, "label": _("Démarrer"), "title": _("Passer en cours de préparation"),
            "icon": "bi-play-fill", "btn": "btn-outline-primary", "confirm": "",
        }
    if commande.delivery_status == Invoice.EN_COURS:
        return {
            "current": Invoice.EN_COURS, "label": _("Livrée"),
            "title": (
                _("Marquer comme livrée")
                if commande.status == Invoice.CONVERTIE
                else _("Marquer comme livrée et valider la commande")
            ),
            "icon": "bi-check2-circle", "btn": "btn-success",
            # Une livraison peut déduire le prix de livraison de la caisse de
            # celui qui clique, et valide la commande (facture, stock) tant
            # qu'elle ne l'est pas : on confirme pour éviter un clic malheureux.
            "confirm": (
                _("Marquer la commande %(number)s comme livrée ?")
                if commande.status == Invoice.CONVERTIE
                else _("Marquer la commande %(number)s comme livrée et la valider (facture générée, stock déduit) ?")
            ) % {"number": commande.commande_seq if commande.commande_seq is not None else commande.number},
        }
    return None


@login_required
def commande_list(request):
    query = request.GET.get("q", "").strip()
    # Plus de vue "Toutes" : la liste est toujours classée sur l'un des
    # trois statuts de livraison (voir Invoice.DELIVERY_STATUS_CHOICES),
    # "en cours" par défaut (voir DEFAULT_COMMANDE_TAB). Une
    # commande annulée ne compte dans aucun des trois, plus rien à
    # préparer/livrer pour elle.
    delivery_filter = request.GET.get("livraison") or DEFAULT_COMMANDE_TAB
    if delivery_filter not in (Invoice.EN_ATTENTE, Invoice.EN_COURS, Invoice.LIVREE):
        delivery_filter = DEFAULT_COMMANDE_TAB

    base = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.COMMANDE)
        .exclude(status=Invoice.ANNULEE)
        .select_related("client", "delivered_by")
    )
    delivery_counts = {
        Invoice.EN_ATTENTE: base.filter(delivery_status=Invoice.EN_ATTENTE).count(),
        Invoice.EN_COURS: base.filter(delivery_status=Invoice.EN_COURS).count(),
        Invoice.LIVREE: base.filter(delivery_status=Invoice.LIVREE).count(),
    }

    commandes = base.filter(delivery_status=delivery_filter)
    if query:
        commandes = commandes.filter(_commande_query(query))
    if not query:
        commandes = commandes[:100]
    commandes = list(commandes)
    for commande in commandes:
        commande.next_action = _commande_next_action(commande)
    return render(
        request, "sales/commande_list.html",
        {
            "commandes": commandes, "query": query,
            "delivery_filter": delivery_filter, "delivery_counts": delivery_counts,
        },
    )


@login_required
def commande_search(request):
    """Recherche commande en direct sur la liste des commandes — même
    principe que catalog:product_search / sales:client_search (au fil de
    la frappe, sans recharger la page) : renvoie du JSON, filtré sur
    l'onglet de livraison actif (voir commande_list) tout comme le rendu
    serveur initial, pour que les deux restent cohérents."""
    query = request.GET.get("q", "").strip()
    delivery_filter = request.GET.get("livraison") or DEFAULT_COMMANDE_TAB
    if delivery_filter not in (Invoice.EN_ATTENTE, Invoice.EN_COURS, Invoice.LIVREE):
        delivery_filter = DEFAULT_COMMANDE_TAB

    commandes = (
        Invoice.objects.filter(
            boutique=request.boutique, type=Invoice.COMMANDE, delivery_status=delivery_filter,
        )
        .exclude(status=Invoice.ANNULEE)
        .select_related("client")
    )
    if query:
        commandes = commandes.filter(_commande_query(query))
    commandes = commandes.order_by("-issue_date", "-created_at")[:100]

    results = [
        {
            "id": str(c.id),
            "number": c.commande_seq if c.commande_seq is not None else c.number,
            "client": c.client.name if c.client else None,
            "status": c.status,
            "status_display": c.get_status_display(),
            "date": c.issue_date.strftime("%d/%m/%Y"),
            "total_ttc": float(c.total_ttc),
            "currency": c.currency,
            "url": reverse("sales:invoice_detail", args=[c.id]),
            "action": _json_action(c),
        }
        for c in commandes
    ]
    return JsonResponse({"results": results})


def _json_action(commande):
    action = _commande_next_action(commande)
    if action is None:
        return None
    return {**action, "url": reverse("sales:commande_advance", args=[commande.id])}


def _preselected_products_for_formset(formset, compte):
    """Infos (nom/image/prix/...) des produits déjà associés aux lignes du
    formset, pour que le JS puisse redessiner la vignette de sélection sans
    requête si le devis est réaffiché après une erreur de validation — la
    recherche en direct ne peut pas retrouver ces infos, le champ produit
    n'étant qu'un id caché."""

    ids = {line_form["product"].value() for line_form in formset}
    ids.discard(None)
    ids.discard("")
    if not ids:
        return {}
    products = Product.objects.filter(id__in=ids, compte=compte).select_related("unit")
    return {
        str(p.id): {
            "name": p.name,
            "sku": p.sku,
            "unit": str(p.unit),
            "price": p.default_sale_price,
            "tva_rate": p.tva_rate,
            "image_url": p.image.url if p.image else None,
        }
        for p in products
    }


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_create(request):
    if request.method == "POST":
        form = InvoiceForm(request.POST, boutique=request.boutique)
        formset = InvoiceLineFormSet(request.POST, form_kwargs={"compte": request.compte})
        if form.is_valid() and formset.is_valid():
            lines_data = _extract_lines_data(formset)
            if not lines_data:
                messages.error(request, _("Ajoutez au moins une ligne au devis."))
            else:
                invoice = services.build_invoice(
                    boutique=request.boutique,
                    client=form.cleaned_data["client"],
                    type=Invoice.DEVIS,
                    created_by=request.user,
                    lines_data=lines_data,
                    discount_amount=Decimal(form.cleaned_data["discount_amount"]),
                    currency=form.cleaned_data["currency"] or request.boutique.devise,
                    pdf_format=form.cleaned_data["pdf_format"],
                )
                if settings.IS_OFFLINE:
                    outbox.enqueue(OutboxEntry.INVOICE, invoice.id)
                messages.success(request, _("%(invoice)s créé en brouillon.") % {"invoice": invoice.number})
                return redirect("sales:invoice_detail", invoice_id=invoice.id)
    else:
        form = InvoiceForm(boutique=request.boutique)
        formset = InvoiceLineFormSet(form_kwargs={"compte": request.compte})

    preselected_products = _preselected_products_for_formset(formset, request.compte)
    return render(
        request,
        "sales/invoice_form.html",
        {
            "form": form,
            "formset": formset,
            "preselected_products": preselected_products,
            "rate_map": request.boutique.exchange_rate_map,
            "document_kind": "devis",
        },
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
def devis_update(request, invoice_id):
    """Modification d'un devis tant qu'il n'est pas encore converti en
    facture (voir services.update_invoice) — même formulaire que
    invoice_create, pré-rempli avec les lignes existantes."""
    devis = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique, type=Invoice.DEVIS)

    if devis.status in (Invoice.CONVERTIE, Invoice.ANNULEE):
        messages.error(request, _("Ce devis n'est plus modifiable."))
        return redirect("sales:invoice_detail", invoice_id=devis.id)

    if settings.IS_OFFLINE:
        # update_invoice() n'est pas rejouable côté push (PushInvoicesView
        # appelle build_invoice(..., id=...), qui ne modifie jamais un
        # Invoice déjà existant) — modifier un devis hors-ligne ne
        # remonterait donc jamais en ligne. Même limitation, même message
        # que devis_generate_invoice.
        messages.error(
            request,
            _(
                "La modification d'un devis n'est pas encore disponible hors-ligne — "
                "utilisez le poste en ligne."
            ),
        )
        return redirect("sales:invoice_detail", invoice_id=devis.id)

    if request.method == "POST":
        form = InvoiceForm(request.POST, boutique=request.boutique)
        formset = InvoiceLineFormSet(request.POST, form_kwargs={"compte": request.compte})
        if form.is_valid() and formset.is_valid():
            lines_data = _extract_lines_data(formset, require_changed=False)
            if not lines_data:
                messages.error(request, _("Ajoutez au moins une ligne au devis."))
            else:
                services.update_invoice(
                    devis,
                    client=form.cleaned_data["client"],
                    lines_data=lines_data,
                    discount_amount=Decimal(form.cleaned_data["discount_amount"]),
                    currency=form.cleaned_data["currency"] or request.boutique.devise,
                    pdf_format=form.cleaned_data["pdf_format"],
                )
                messages.success(request, _("%(invoice)s modifié.") % {"invoice": devis.number})
                return redirect("sales:invoice_detail", invoice_id=devis.id)
    else:
        form = InvoiceForm(
            boutique=request.boutique,
            initial={
                "client": devis.client_id,
                "currency": devis.currency,
                "discount_amount": devis.discount_amount,
                "pdf_format": devis.pdf_format,
            },
        )
        line_initial = [
            {
                "product": line.product_id,
                "description": line.description,
                "quantity": line.quantity,
                "unit_price_ht": line.unit_price_ht,
                "tva_rate": line.tva_rate,
                "discount_amount": line.discount_amount,
            }
            for line in devis.lines.all()
        ]
        formset = InvoiceLineFormSet(initial=line_initial, form_kwargs={"compte": request.compte})

    preselected_products = _preselected_products_for_formset(formset, request.compte)
    return render(
        request,
        "sales/invoice_form.html",
        {
            "form": form,
            "formset": formset,
            "preselected_products": preselected_products,
            "rate_map": request.boutique.exchange_rate_map,
            "document_kind": "devis",
            "editing_invoice": devis,
        },
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
def commande_create(request):
    """Même mécanique que invoice_create (devis) : la commande est créée en
    BROUILLON, sans impact sur le stock — voir services.build_invoice. Seul
    le type diffère ; convert_commande_to_invoice() s'occupe de la
    conversion en facture (stock déduit à ce moment-là)."""
    if request.method == "POST":
        form = InvoiceForm(request.POST, boutique=request.boutique)
        formset = InvoiceLineFormSet(request.POST, form_kwargs={"compte": request.compte})
        if form.is_valid() and formset.is_valid():
            lines_data = _extract_lines_data(formset)
            if not lines_data:
                messages.error(request, _("Ajoutez au moins une ligne à la commande."))
            else:
                invoice = services.build_invoice(
                    boutique=request.boutique,
                    client=form.cleaned_data["client"],
                    type=Invoice.COMMANDE,
                    created_by=request.user,
                    lines_data=lines_data,
                    discount_amount=Decimal(form.cleaned_data["discount_amount"]),
                    currency=form.cleaned_data["currency"] or request.boutique.devise,
                    note=form.cleaned_data["note"],
                )

                deposit_amount = min(form.cleaned_data["deposit_amount"], invoice.total_ttc)
                if deposit_amount > 0:
                    payment_id = uuid.uuid4()
                    services.record_payment(
                        invoice, amount=deposit_amount, method=Payment.ESPECES,
                        created_by=request.user, id=payment_id,
                    )
                    if settings.IS_OFFLINE:
                        outbox.enqueue(OutboxEntry.PAYMENT, payment_id)

                if settings.IS_OFFLINE:
                    outbox.enqueue(OutboxEntry.INVOICE, invoice.id)
                messages.success(request, _("%(invoice)s créée en brouillon.") % {"invoice": invoice.number})
                return redirect("sales:invoice_detail", invoice_id=invoice.id)
    else:
        form = InvoiceForm(boutique=request.boutique)
        formset = InvoiceLineFormSet(form_kwargs={"compte": request.compte})

    preselected_products = _preselected_products_for_formset(formset, request.compte)
    return render(
        request,
        "sales/invoice_form.html",
        {
            "form": form,
            "formset": formset,
            "preselected_products": preselected_products,
            "rate_map": request.boutique.exchange_rate_map,
            "document_kind": "commande",
        },
    )


def _public_pdf_url(request, invoice):
    return request.build_absolute_uri(reverse("sales:invoice_public_pdf", args=[invoice.id]))


def _shorten(request, target_path):
    """Remplace un chemin par un lien court /s/<code>/ — les URL publiques
    envoyées par WhatsApp (UUID inclus) sont sinon assez longues pour
    alourdir le message. Auto-hébergé (voir ShortLink), pas de service
    tiers : fonctionne aussi hors-ligne et n'expose rien à un tiers."""
    link = ShortLink.get_or_create_for_path(target_path)
    return request.build_absolute_uri(reverse("core:short_link", args=[link.code]))


def _public_view_url(request, invoice):
    """Lien unique envoyé au client — ouvre directement le PDF (ticket/A4),
    pas la page web publique : le client doit voir le document tel quel
    (mêmes infos que le reçu papier), pas une mise en page web séparée."""
    return _shorten(request, reverse("sales:invoice_public_pdf", args=[invoice.id]))


@login_required
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines__product", "payments"),
        id=invoice_id,
        boutique=request.boutique,
    )
    payment_form = PaymentForm()
    pay_form = InvoicePaymentForm()

    whatsapp_url = None
    has_client_phone = bool(invoice.client and invoice.client.phone)
    if has_client_phone:
        whatsapp_url = build_share_link(
            phone=invoice.client.phone,
            invoice=invoice,
            view_url=_public_view_url(request, invoice),
        )

    converted_invoice = None
    if invoice.type in (Invoice.DEVIS, Invoice.COMMANDE) and invoice.status == Invoice.CONVERTIE:
        converted_invoice = invoice.conversions.filter(type=Invoice.FACTURE).first()

    return render(
        request,
        "sales/invoice_detail.html",
        {
            "invoice": invoice,
            "payment_form": payment_form,
            "pay_form": pay_form,
            "whatsapp_url": whatsapp_url,
            "whatsapp_api_configured": is_configured(),
            "has_client_phone": has_client_phone,
            "last_reminder": reminders.last_reminder_at(invoice=invoice) if invoice.type == Invoice.FACTURE else None,
            "converted_invoice": converted_invoice,
        },
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_send_whatsapp(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique"), id=invoice_id, boutique=request.boutique
    )
    phone = (
        normalize_phone(invoice.client.phone, country_calling_code=invoice.boutique.country_calling_code)
        if invoice.client
        else None
    )
    if not phone:
        messages.error(request, _("Ce client n'a pas de numéro de téléphone."))
        return redirect("sales:invoice_detail", invoice_id=invoice.id)

    pdf_url = _public_pdf_url(request, invoice)
    view_url = _public_view_url(request, invoice)
    try:
        send_document(
            to=phone,
            message=build_message(invoice, view_link=view_url),
            document_url=pdf_url,
            filename=f"{invoice.number}.pdf",
        )
    except WhatsAppSendError as exc:
        messages.error(request, _("Échec de l'envoi WhatsApp : %(error)s") % {"error": exc})
    else:
        messages.success(
            request,
            _("Facture envoyée par WhatsApp à %(client)s.") % {"client": invoice.client.name},
        )
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_validate(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique)
    services.validate_invoice(invoice, created_by=request.user)
    messages.success(request, _("%(invoice)s validé.") % {"invoice": invoice.number})
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def devis_generate_invoice(request, invoice_id):
    """Transforme un devis validé en facture réelle — le client a dit oui,
    on facture pour de vrai (stock déduit) avec le même choix paiement
    complet/acompte que juste après une vente (voir sale_create)."""
    devis = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique, type=Invoice.DEVIS)
    if devis.status != Invoice.VALIDEE:
        messages.error(request, _("Validez d'abord le devis avant de générer la facture."))
        return redirect("sales:invoice_detail", invoice_id=devis.id)

    if settings.IS_OFFLINE:
        # convert_devis_to_invoice() n'est pas encore rejouable (pas
        # d'id=/created_at=) et il n'existe aucun endpoint de push capable
        # de reproduire "convertir + déduire le stock + marquer CONVERTIE"
        # côté serveur — créer cette facture hors-ligne la condamnerait à
        # ne jamais se synchroniser.
        messages.error(
            request,
            _(
                "La conversion de devis en facture n'est pas encore disponible hors-ligne — "
                "utilisez le poste en ligne."
            ),
        )
        return redirect("sales:invoice_detail", invoice_id=devis.id)

    if request.method == "POST":
        payment_type = request.POST.get("payment_type", "full")
        try:
            deposit_amount = Decimal(request.POST.get("deposit_amount") or "0")
        except InvalidOperation:
            deposit_amount = Decimal("0")

        invoice = services.convert_devis_to_invoice(devis, created_by=request.user)
        paid_amount = min(deposit_amount, invoice.total_ttc) if payment_type == "partial" else invoice.total_ttc

        if paid_amount > 0:
            services.record_payment(
                invoice, amount=paid_amount, method=Payment.ESPECES, created_by=request.user,
            )

        messages.success(
            request,
            _("%(invoice)s générée depuis %(devis)s.") % {"invoice": invoice.number, "devis": devis.number},
        )
        return redirect("sales:invoice_detail", invoice_id=invoice.id)

    return redirect("sales:invoice_detail", invoice_id=devis.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def commande_generate_invoice(request, invoice_id):
    """Valide une commande : l'acompte éventuel déjà saisi (formulaire de
    paiement générique, voir payment_create) est reporté sur la facture par
    services.convert_commande_to_invoice(), puis on demande — comme pour
    une vente ou un devis — si le client règle le solde restant maintenant
    ou ne fait (encore) qu'un acompte. La facture est marquée payée
    seulement si le solde est réellement couvert ; sinon elle reste
    partiellement payée, exactement comme une vente à acompte."""
    commande = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique, type=Invoice.COMMANDE)
    if commande.status in (Invoice.CONVERTIE, Invoice.ANNULEE):
        messages.error(request, _("Cette commande ne peut plus être validée."))
        return redirect("sales:invoice_detail", invoice_id=commande.id)

    if settings.IS_OFFLINE:
        # Même limite que devis_generate_invoice : convert_commande_to_invoice()
        # n'est pas rejouable (pas d'id=/created_at=) et il n'existe aucun
        # endpoint de push capable de reproduire "convertir + déduire le
        # stock + marquer CONVERTIE" côté serveur.
        messages.error(
            request,
            _(
                "La validation de commande en facture n'est pas encore disponible hors-ligne — "
                "utilisez le poste en ligne."
            ),
        )
        return redirect("sales:invoice_detail", invoice_id=commande.id)

    if request.method == "POST":
        payment_type = request.POST.get("payment_type", "full")
        try:
            deposit_amount = Decimal(request.POST.get("deposit_amount") or "0")
        except InvalidOperation:
            deposit_amount = Decimal("0")

        invoice = services.convert_commande_to_invoice(commande, created_by=request.user)

        # Le solde restant (pas invoice.total_ttc) : un acompte déjà versé
        # sur la commande a pu être reporté sur la facture ci-dessus, il ne
        # faut pas redemander de payer une seconde fois cette part-là.
        remaining = invoice.balance_due
        paid_amount = min(deposit_amount, remaining) if payment_type == "partial" else remaining

        if paid_amount > 0:
            services.record_payment(
                invoice, amount=paid_amount, method=Payment.ESPECES, created_by=request.user,
            )

        messages.success(
            request,
            _("%(invoice)s générée depuis %(commande)s.") % {"invoice": invoice.number, "commande": commande.number},
        )
        return redirect("sales:invoice_detail", invoice_id=invoice.id)

    return redirect("sales:invoice_detail", invoice_id=commande.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def commande_mark_delivered(request, invoice_id):
    """Marque la commande comme livrée ET la valide si elle ne l'est pas
    encore (voir services.deliver_commande) : facture générée, stock
    déduit. Le paiement, lui, reste à part (bouton « Encaisser » de la
    facture). Enregistre qui a fait la livraison, pour traçabilité."""
    if request.method == "POST":
        with transaction.atomic():
            commande = get_object_or_404(
                Invoice.objects.select_for_update(), id=invoice_id, boutique=request.boutique, type=Invoice.COMMANDE,
            )
            _deliver_and_validate(request, commande)
    return redirect("sales:invoice_detail", invoice_id=invoice_id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def commande_mark_en_cours(request, invoice_id):
    """Fait passer la commande en préparation — depuis 'en attente' (on
    commence à la préparer, ouvert à tout le personnel habilité) ou pour
    annuler un marquage 'livrée' fait par erreur (réservé aux
    administrateurs de l'entreprise : une fois la commande remise au
    client, revenir en arrière ne doit pas être à la portée de n'importe
    quel caissier/gérant). Voir services.mark_commande_en_cours."""
    commande = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique, type=Invoice.COMMANDE)
    if request.method == "POST":
        if commande.delivery_status == Invoice.LIVREE and not request.is_compte_admin:
            messages.error(
                request,
                _("Seul un administrateur de l'entreprise peut annuler une commande marquée comme livrée."),
            )
            return redirect("sales:invoice_detail", invoice_id=commande.id)
        services.mark_commande_en_cours(commande, undone_by=request.user)
        messages.success(request, _("%(commande)s remise en cours de préparation.") % {"commande": commande.number})
    return redirect("sales:invoice_detail", invoice_id=commande.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
@require_POST
def commande_advance(request, invoice_id):
    """Bouton « Action » de la liste des commandes : fait avancer d'UNE
    étape (en attente -> en cours -> livrée). Contrairement à
    commande_mark_en_cours, ne recule jamais : le formulaire envoie l'état
    que l'utilisateur voyait (`from`) et rien n'est modifié si la commande
    a changé entre-temps — sinon un clic sur une liste périmée pourrait,
    pour un administrateur, annuler la livraison qu'un collègue vient de
    faire. La commande est verrouillée le temps de la vérification."""
    seen = request.POST.get("from", "")
    with transaction.atomic():
        commande = get_object_or_404(
            Invoice.objects.select_for_update(), id=invoice_id, boutique=request.boutique, type=Invoice.COMMANDE,
        )
        if commande.status == Invoice.ANNULEE:
            messages.error(request, _("Cette commande est annulée."))
        elif commande.delivery_status != seen:
            messages.warning(
                request,
                _("%(commande)s a déjà changé de statut (%(status)s) — rien n'a été modifié.") % {
                    "commande": commande.number, "status": commande.get_delivery_status_display(),
                },
            )
        elif seen == Invoice.EN_ATTENTE:
            services.mark_commande_en_cours(commande)
            messages.success(request, _("%(commande)s passée en cours.") % {"commande": commande.number})
        elif seen == Invoice.EN_COURS:
            _deliver_and_validate(request, commande)

    target = request.POST.get("next", "")
    if not (target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    )):
        target = reverse("sales:commande_list")
    return redirect(target)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def payment_create(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique)
    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            # id pré-généré (plutôt que de changer le contrat de retour de
            # record_payment, qui renvoie la facture et est utilisé
            # ailleurs) pour pouvoir mettre ce paiement en file d'attente
            # de synchro offline (outbox.enqueue ci-dessous).
            payment_id = uuid.uuid4()
            services.record_payment(
                invoice,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                created_by=request.user,
                id=payment_id,
            )
            if settings.IS_OFFLINE:
                outbox.enqueue(OutboxEntry.PAYMENT, payment_id)
            messages.success(request, _("Paiement enregistré."))
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_pay(request, invoice_id):
    """Encaisse un versement sur une facture non entièrement payée. Le
    montant est libre (un client peut simplement réduire sa dette) mais ne
    peut jamais dépasser le reste à payer, recalculé ici sur la facture
    verrouillée — pas sur ce que le navigateur affichait : un formulaire
    périmé ou un double clic ne peut donc pas encaisser plus que dû. Un
    versement partiel laisse la facture « partiellement payée » ; celui qui
    couvre le reste la solde (voir services.record_payment)."""
    if request.method != "POST":
        return redirect("sales:invoice_detail", invoice_id=invoice_id)

    form = InvoicePaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Versement invalide — vérifiez le montant et le mode de paiement."))
        return redirect("sales:invoice_detail", invoice_id=invoice_id)
    amount = form.cleaned_data["amount"]

    with transaction.atomic():
        invoice = get_object_or_404(
            Invoice.objects.select_for_update(), id=invoice_id, boutique=request.boutique,
        )
        if invoice.type != Invoice.FACTURE or invoice.status in (Invoice.ANNULEE, Invoice.BROUILLON):
            messages.error(request, _("Cette facture n'accepte pas de paiement."))
            return redirect("sales:invoice_detail", invoice_id=invoice.id)

        remaining = invoice.balance_due
        if remaining <= 0:
            messages.info(request, _("Cette facture est déjà soldée."))
            return redirect("sales:invoice_detail", invoice_id=invoice.id)
        if amount > remaining:
            messages.error(
                request,
                _("Le versement (%(amount)s) dépasse le reste à payer (%(remaining)s %(currency)s).") % {
                    "amount": amount, "remaining": remaining, "currency": invoice.currency,
                },
            )
            return redirect("sales:invoice_detail", invoice_id=invoice.id)

        payment_id = uuid.uuid4()
        services.record_payment(
            invoice,
            amount=amount,
            method=form.cleaned_data["method"],
            reference=form.cleaned_data["reference"],
            created_by=request.user,
            id=payment_id,
        )
        if settings.IS_OFFLINE:
            outbox.enqueue(OutboxEntry.PAYMENT, payment_id)

    left = remaining - amount
    if left <= 0:
        messages.success(
            request,
            _("%(invoice)s soldée : %(amount)s %(currency)s encaissés.") % {
                "invoice": invoice.number, "amount": amount, "currency": invoice.currency,
            },
        )
    else:
        messages.success(
            request,
            _("Versement de %(amount)s %(currency)s enregistré — reste à payer : %(left)s %(currency)s.") % {
                "amount": amount, "currency": invoice.currency, "left": left,
            },
        )
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@xframe_options_exempt
def invoice_pdf(request, invoice_id):
    # Sans cette exemption, Django ajoute X-Frame-Options: DENY par défaut
    # sur toute réponse — bloquant silencieusement l'affichage du PDF dans
    # l'<iframe> de prévisualisation ci-dessous et dans le bouton
    # "Imprimer" (qui charge ce même PDF dans un <iframe> caché avant
    # d'appeler print()) : le navigateur refuse la frame, l'iframe reste
    # sur about:blank, et contentWindow.print() lève une SecurityError.
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines"),
        id=invoice_id,
        boutique=request.boutique,
    )
    pdf_bytes = render_invoice_pdf(invoice)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
    return response


def invoice_public_pdf(request, invoice_id):
    """Lien public (sans connexion) utilisé pour le partage WhatsApp — la
    sécurité repose sur le caractère non devinable de l'UUID de la facture,
    comme un lien de partage classique (Google Docs, Stripe...). Pas de
    scoping par boutique/compte ici puisque le visiteur n'est pas connecté."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines", "payments"),
        id=invoice_id,
    )
    pdf_bytes = render_invoice_pdf(invoice)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
    return response


def invoice_public_view(request, invoice_id):
    """Page web publique (sans connexion), même principe de sécurité que
    invoice_public_pdf (UUID non devinable) : prix, quantités, total —
    le lien "détails" envoyé au client, distinct du lien galerie photos."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines__product"),
        id=invoice_id,
    )
    return render(request, "sales/invoice_public.html", {"invoice": invoice})


def invoice_public_gallery(request, invoice_id):
    """Page web publique (sans connexion) dédiée aux photos : pour chaque
    produit de la commande, toutes ses images (principale + les jusqu'à 2
    supplémentaires), pas seulement la première — second lien, séparé de
    celui des prix, envoyé au client pour qu'il voie les articles."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related(
            "lines__product__extra_images"
        ),
        id=invoice_id,
    )
    products = []
    seen_ids = set()
    for line in invoice.lines.all():
        if line.product_id and line.product_id not in seen_ids:
            seen_ids.add(line.product_id)
            products.append(line.product)
    return render(
        request, "sales/invoice_public_gallery.html", {"invoice": invoice, "products": products}
    )


# --- Rapport de marge -----------------------------------------------------

MARGIN_REPORT_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE)


@login_required
@boutique_role_required(*MARGIN_REPORT_ROLES)
def margin_report(request):
    """Bénéfice par produit sur une période (par défaut : le mois en cours).
    Réservé aux administrateurs et gérants : il révèle les prix d'achat."""
    today = timezone.localdate()
    date_from = parse_date(request.GET.get("from", "") or "") or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "") or "") or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    last_month_end = today.replace(day=1) - timedelta(days=1)
    presets = [
        (_("Aujourd'hui"), today, today),
        (_("7 derniers jours"), today - timedelta(days=6), today),
        (_("Ce mois"), today.replace(day=1), today),
        (_("Mois dernier"), last_month_end.replace(day=1), last_month_end),
    ]
    report = reports.margin_report(request.boutique, date_from, date_to)
    return render(request, "sales/margin_report.html", {
        "date_from": date_from, "date_to": date_to, "presets": presets,
        "rows": report["rows"], "totals": report["totals"],
        "expenses_by_category": report["expenses_by_category"],
    })


# --- Relances de paiement (WhatsApp) --------------------------------------

def _reminder_redirect(request, fallback):
    """Retour à la page d'où vient la demande (`next`), si elle est sûre."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(fallback)


def _dispatch_reminder(request, *, client, message, invoice=None, amount, currency, fallback, document_url=None,
                       filename=None):
    """Envoie la relance : par l'API WhatsApp si elle est configurée (avec le
    PDF pour une facture), sinon ouvre WhatsApp avec le texte pré-rempli.
    Dans les deux cas la relance est notée (dernière relance affichée). Avec
    le lien wa.me, on ne sait pas si le message part vraiment : on note qu'il
    a été *ouvert*, ce qui suffit à ne pas relancer deux fois de suite."""
    boutique = request.boutique
    phone = normalize_phone(client.phone, country_calling_code=boutique.country_calling_code)
    if not phone:
        messages.error(request, _("Ce client n'a pas de numéro de téléphone."))
        return _reminder_redirect(request, fallback)
    if settings.IS_OFFLINE:
        # Les liens envoyés (PDF de la facture) pointeraient vers le poste
        # local, injoignable pour le client.
        messages.error(request, _("Les relances s'envoient depuis le poste en ligne."))
        return _reminder_redirect(request, fallback)

    def record(channel):
        PaymentReminder.objects.create(
            boutique=boutique, client=client, invoice=invoice, amount=amount, currency=currency,
            channel=channel, sent_by=request.user,
        )

    if is_configured():
        try:
            send_document(to=phone, message=message, document_url=document_url, filename=filename)
        except WhatsAppSendError as exc:
            messages.error(request, _("Échec de l'envoi WhatsApp : %(error)s") % {"error": exc})
        else:
            record(PaymentReminder.API)
            messages.success(request, _("Relance envoyée à %(client)s.") % {"client": client.name})
        return _reminder_redirect(request, fallback)

    link = build_link_for_message(
        phone=client.phone, message=message, country_calling_code=boutique.country_calling_code,
    )
    record(PaymentReminder.LINK)
    return redirect(link)


@login_required
@boutique_role_required(*MANAGE_ROLES)
@require_POST
def invoice_remind(request, invoice_id):
    """Relance pour une facture : montant restant + lien vers la facture."""
    invoice = reminders.unpaid_invoices(request.boutique).filter(id=invoice_id).first()
    if invoice is None:
        messages.error(request, _("Cette facture n'a rien à relancer (soldée, annulée ou sans client)."))
        return _reminder_redirect(request, reverse("sales:debtor_list"))
    message = reminders.build_invoice_reminder(
        invoice, due=invoice.due, view_link=_public_view_url(request, invoice),
    )
    return _dispatch_reminder(
        request, client=invoice.client, message=message, invoice=invoice, amount=invoice.due,
        currency=invoice.currency, fallback=reverse("sales:invoice_detail", args=[invoice.id]),
        document_url=_public_pdf_url(request, invoice), filename=f"{invoice.number}.pdf",
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
@require_POST
def client_remind(request, client_id):
    """Relance d'un client pour l'ensemble de ses factures impayées (relevé)."""
    client = get_object_or_404(Client, id=client_id, boutique=request.boutique)
    invoices = list(reminders.unpaid_invoices(request.boutique, client=client))
    fallback = reverse("sales:client_statement", args=[client.id])
    if not invoices:
        messages.info(request, _("Ce client n'a aucune facture impayée."))
        return _reminder_redirect(request, fallback)
    message = reminders.build_statement(
        client, invoices, boutique=request.boutique, link_for=lambda invoice: _public_view_url(request, invoice),
    )
    return _dispatch_reminder(
        request, client=client, message=message, amount=sum(i.due for i in invoices),
        currency=invoices[0].currency, fallback=fallback,
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
def debtor_list(request):
    """Clients qui nous doivent de l'argent, du plus gros débiteur au plus
    petit, avec de quoi les relancer en un clic."""
    rows = reminders.debtors(request.boutique)
    totals = {}
    for row in rows:
        for currency, amount in row["balances"]:
            totals[currency] = totals.get(currency, 0) + amount
    return render(request, "sales/debtor_list.html", {
        "rows": rows, "totals": sorted(totals.items()), "now": timezone.now(),
        "whatsapp_api_configured": is_configured(),
    })


@login_required
@boutique_role_required(*MANAGE_ROLES)
def client_statement(request, client_id):
    """Relevé d'un client : ses factures impayées, ce qu'il doit, l'historique
    des relances."""
    client = get_object_or_404(Client, id=client_id, boutique=request.boutique)
    invoices = list(reminders.unpaid_invoices(request.boutique, client=client))
    totals = {}
    for invoice in invoices:
        totals[invoice.currency] = totals.get(invoice.currency, 0) + invoice.due
    return render(request, "sales/client_statement.html", {
        "client": client, "invoices": invoices, "totals": sorted(totals.items()),
        "reminder_history": client.reminders.select_related("sent_by", "invoice")[:10],
        "last_reminder": reminders.last_reminder_at(client=client),
        "today": timezone.localdate(), "now": timezone.now(),
        "whatsapp_api_configured": is_configured(),
    })
