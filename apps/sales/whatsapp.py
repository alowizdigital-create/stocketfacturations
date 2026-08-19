"""Construction du message et du lien de partage WhatsApp pour une
facture/devis. Un seul lien envoyé au client — la page de détails (prix,
quantités, total, avec la description/photo de chaque produit) — voir
apps.sales.views._public_view_url. Deux usages :
  - build_share_link() : lien "click to chat" (wa.me), utilisé en secours
    quand l'API techsoft n'est pas configurée — ne peut que pré-remplir un
    texte, pas joindre de fichier.
  - build_message() : texte réutilisé par apps.sales.whatsapp_api pour
    l'envoi API avec le PDF en pièce jointe."""

import re
from urllib.parse import quote

WA_ME_BASE = "https://wa.me/"


def normalize_phone(raw_phone, country_calling_code=None):
    """Ne garde que les chiffres, tel qu'attendu par les API WhatsApp.
    Renvoie None si le numéro est vide/inexploitable.

    Un numéro déjà saisi au format international (+225... ou 00225...)
    est laissé tel quel. Sinon, si la boutique a un indicatif pays
    configuré (voir Boutique.country_calling_code), il est ajouté devant
    le numéro local — nécessaire pour que le lien WhatsApp fonctionne
    quel que soit le pays d'Afrique où l'entreprise opère, même quand le
    personnel saisit les numéros sans code pays (habitude courante)."""
    if not raw_phone:
        return None
    digits = re.sub(r"[^\d+]", "", raw_phone)
    if digits.startswith("+"):
        digits = digits.lstrip("+")
    elif digits.startswith("00"):
        digits = digits[2:]
    elif country_calling_code and not digits.startswith(country_calling_code):
        local = digits.lstrip("0")
        return f"{country_calling_code}{local}" if local else None

    # Erreur de saisie courante : le 0 de tronc local est laissé collé
    # juste après l'indicatif pays (ex: "+225 07..." au lieu de "+225 7...")
    # — en E.164 ce zéro ne doit jamais apparaître, on le retire.
    if country_calling_code and digits.startswith(country_calling_code + "0"):
        digits = country_calling_code + digits[len(country_calling_code) + 1:]

    return digits or None


def build_message(invoice, *, view_link=None):
    title = "facture" if invoice.type == invoice.FACTURE else "devis"
    client_name = invoice.client.name if invoice.client else ""
    greeting = f"Bonjour {client_name}, " if client_name else "Bonjour, "
    amount = f"{invoice.total_ttc:,.0f}".replace(",", " ")
    message = (
        f"{greeting}voici votre {title} {invoice.number} de {invoice.boutique.name} "
        f"({amount} {invoice.currency})."
    )
    if view_link:
        message += f"\nDétails : {view_link}"
    return message


def build_share_link(*, phone, invoice, view_url):
    number = normalize_phone(phone, country_calling_code=invoice.boutique.country_calling_code)
    if number is None:
        return None
    message = build_message(invoice, view_link=view_url)
    return f"{WA_ME_BASE}{number}?text={quote(message)}"
