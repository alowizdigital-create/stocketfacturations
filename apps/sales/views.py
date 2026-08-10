import csv
import io
import json
import quopri
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.permissions import boutique_role_required
from apps.sync import outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from . import services
from .forms import ClientForm, ClientImportForm, InvoiceForm, InvoiceLineFormSet, PaymentForm, SaleForm
from .models import Client, Invoice, Payment, Sale
from .pdf import render_invoice_pdf
from .whatsapp import build_message, build_share_link, normalize_phone
from .whatsapp_api import WhatsAppSendError, is_configured, send_document

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE, Membership.CAISSIER)


@login_required
def client_list(request):
    clients = Client.objects.filter(boutique=request.boutique).order_by("name")
    return render(request, "sales/client_list.html", {"clients": clients})


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
            messages.success(request, "Client créé.")
            return redirect("sales:client_list")
    else:
        form = ClientForm()
    return render(request, "sales/client_form.html", {"form": form})


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
        return None, "Fichier CSV vide ou illisible."

    field_map = {}
    for raw_header in reader.fieldnames:
        key = CLIENT_IMPORT_HEADER_ALIASES.get(raw_header.strip().lower())
        if key:
            field_map[raw_header] = key

    if "name" not in field_map.values():
        return None, (
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
        return None, "Aucun contact trouvé dans le fichier vCard."
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
                    messages.error(request, "Le fichier doit être encodé en UTF-8.")
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
                messages.success(
                    request,
                    f"{len(to_create)} client(s) importé(s)."
                    + (f" {skipped} ligne(s) ignorée(s) (doublon ou nom manquant)." if skipped else ""),
                )
            else:
                messages.warning(request, "Aucun nouveau client importé (doublons ou fichier vide).")
            return redirect("sales:client_list")
    else:
        form = ClientImportForm()
    return render(request, "sales/client_import.html", {"form": form})


def _extract_lines_data(formset):
    lines_data = []
    for form in formset:
        if not form.has_changed() or form.cleaned_data.get("DELETE"):
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
    sales = Sale.objects.filter(boutique=request.boutique).select_related("client", "invoice")
    return render(request, "sales/sale_list.html", {"sales": sales})


def _parse_cart(request):
    """Décode le panier envoyé par l'écran de caisse (JSON construit côté
    JS). Le produit et sa TVA sont toujours relus en base — jamais fait
    confiance au JSON pour ces valeurs — seule la quantité et le prix
    unitaire (modifiable par le caissier) viennent du client."""

    raw = request.POST.get("cart_json", "")
    try:
        items = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return None, "Panier invalide."

    if not items:
        return None, "Ajoutez au moins un produit au panier."

    product_ids = [item.get("product_id") for item in items]
    products = Product.objects.filter(id__in=product_ids, compte=request.compte, is_active=True)
    products_by_id = {str(p.id): p for p in products}

    lines_data = []
    for item in items:
        product = products_by_id.get(str(item.get("product_id")))
        if product is None:
            return None, "Un produit du panier n'existe plus."
        try:
            quantity = Decimal(str(item.get("quantity", "0")))
            unit_price_ht = Decimal(str(item.get("unit_price_ht", "0")))
        except InvalidOperation:
            return None, "Quantité ou prix invalide dans le panier."
        if quantity <= 0 or unit_price_ht < 0 or quantity != quantity.to_integral_value():
            return None, "Quantité ou prix invalide dans le panier."

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

            messages.success(request, f"{sale.number} enregistrée et {invoice.number} générée.")
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
    messages.success(request, f"{sale.number} confirmée, stock mis à jour.")
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
    messages.success(request, f"Facture {invoice.number} générée.")
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
    invoices = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.FACTURE)
        .select_related("client")
    )
    return render(request, "sales/invoice_list.html", {"invoices": invoices})


@login_required
def devis_list(request):
    devis = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.DEVIS)
        .select_related("client")
    )
    return render(request, "sales/devis_list.html", {"devis": devis})


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
                messages.error(request, "Ajoutez au moins une ligne au devis.")
            else:
                invoice = services.build_invoice(
                    boutique=request.boutique,
                    client=form.cleaned_data["client"],
                    type=Invoice.DEVIS,
                    created_by=request.user,
                    lines_data=lines_data,
                    discount_amount=Decimal(form.cleaned_data["discount_amount"]),
                    currency=form.cleaned_data["currency"] or request.boutique.devise,
                )
                if settings.IS_OFFLINE:
                    outbox.enqueue(OutboxEntry.INVOICE, invoice.id)
                messages.success(request, f"{invoice.number} créé en brouillon.")
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
        },
    )


def _public_pdf_url(request, invoice):
    return request.build_absolute_uri(reverse("sales:invoice_public_pdf", args=[invoice.id]))


def _public_view_url(request, invoice):
    """Page web publique (sans connexion) montrant le devis/facture — prix,
    quantités, total — c'est le lien "détails" envoyé au client."""
    return request.build_absolute_uri(reverse("sales:invoice_public_view", args=[invoice.id]))


def _public_gallery_url(request, invoice):
    """Page web publique (sans connexion) dédiée aux photos : toutes les
    images de chaque produit de la commande (photo principale + photos
    supplémentaires), pas seulement la première — c'est le second lien
    envoyé au client, séparé de celui des prix."""
    return request.build_absolute_uri(reverse("sales:invoice_public_gallery", args=[invoice.id]))


@login_required
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines", "payments"),
        id=invoice_id,
        boutique=request.boutique,
    )
    payment_form = PaymentForm()

    whatsapp_url = None
    has_client_phone = bool(invoice.client and invoice.client.phone)
    if has_client_phone:
        whatsapp_url = build_share_link(
            phone=invoice.client.phone,
            invoice=invoice,
            view_url=_public_view_url(request, invoice),
            gallery_url=_public_gallery_url(request, invoice),
        )

    converted_invoice = None
    if invoice.type == Invoice.DEVIS and invoice.status == Invoice.CONVERTIE:
        converted_invoice = invoice.conversions.filter(type=Invoice.FACTURE).first()

    return render(
        request,
        "sales/invoice_detail.html",
        {
            "invoice": invoice,
            "payment_form": payment_form,
            "whatsapp_url": whatsapp_url,
            "whatsapp_api_configured": is_configured(),
            "has_client_phone": has_client_phone,
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
        messages.error(request, "Ce client n'a pas de numéro de téléphone.")
        return redirect("sales:invoice_detail", invoice_id=invoice.id)

    pdf_url = _public_pdf_url(request, invoice)
    view_url = _public_view_url(request, invoice)
    gallery_url = _public_gallery_url(request, invoice)
    try:
        send_document(
            to=phone,
            message=build_message(invoice, view_link=view_url, gallery_link=gallery_url),
            document_url=pdf_url,
            filename=f"{invoice.number}.pdf",
        )
    except WhatsAppSendError as exc:
        messages.error(request, f"Échec de l'envoi WhatsApp : {exc}")
    else:
        messages.success(request, f"Facture envoyée par WhatsApp à {invoice.client.name}.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_validate(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique)
    services.validate_invoice(invoice, created_by=request.user)
    messages.success(request, f"{invoice.number} validé.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def devis_generate_invoice(request, invoice_id):
    """Transforme un devis validé en facture réelle — le client a dit oui,
    on facture pour de vrai (stock déduit) avec le même choix paiement
    complet/acompte que juste après une vente (voir sale_create)."""
    devis = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique, type=Invoice.DEVIS)
    if devis.status != Invoice.VALIDEE:
        messages.error(request, "Validez d'abord le devis avant de générer la facture.")
        return redirect("sales:invoice_detail", invoice_id=devis.id)

    if settings.IS_OFFLINE:
        # convert_devis_to_invoice() n'est pas encore rejouable (pas
        # d'id=/created_at=) et il n'existe aucun endpoint de push capable
        # de reproduire "convertir + déduire le stock + marquer CONVERTIE"
        # côté serveur — créer cette facture hors-ligne la condamnerait à
        # ne jamais se synchroniser.
        messages.error(
            request,
            "La conversion de devis en facture n'est pas encore disponible hors-ligne — "
            "utilisez le poste en ligne.",
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

        messages.success(request, f"{invoice.number} générée depuis {devis.number}.")
        return redirect("sales:invoice_detail", invoice_id=invoice.id)

    return redirect("sales:invoice_detail", invoice_id=devis.id)


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
            messages.success(request, "Paiement enregistré.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
def invoice_pdf(request, invoice_id):
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
