"""Construction du message et du lien de partage WhatsApp pour une
facture/devis. Deux usages :
  - build_share_link() : lien "click to chat" (wa.me), utilisé en secours
    quand l'API techsoft n'est pas configurée — ne peut que pré-remplir un
    texte, pas joindre de fichier.
  - build_message() : texte réutilisé par apps.sales.whatsapp_api pour
    l'envoi API avec le PDF en pièce jointe."""

import re
from urllib.parse import quote

WA_ME_BASE = "https://wa.me/"


def normalize_phone(raw_phone):
    """Ne garde que les chiffres (et un éventuel + initial), tel
    qu'attendu par les API WhatsApp. Renvoie None si le numéro est
    vide/inexploitable."""
    if not raw_phone:
        return None
    digits = re.sub(r"[^\d+]", "", raw_phone)
    digits = digits.lstrip("+")
    return digits or None


def build_message(invoice, *, with_link=None):
    title = "facture" if invoice.type == invoice.FACTURE else "devis"
    client_name = invoice.client.name if invoice.client else ""
    greeting = f"Bonjour {client_name}, " if client_name else "Bonjour, "
    amount = f"{invoice.total_ttc:,.0f}".replace(",", " ")
    message = (
        f"{greeting}voici votre {title} {invoice.number} de {invoice.boutique.name} "
        f"({amount} {invoice.currency})."
    )
    if with_link:
        message += f"\n{with_link}"
    return message


def build_share_link(*, phone, invoice, pdf_url):
    number = normalize_phone(phone)
    if number is None:
        return None
    message = build_message(invoice, with_link=pdf_url)
    return f"{WA_ME_BASE}{number}?text={quote(message)}"
