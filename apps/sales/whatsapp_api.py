"""Envoi du PDF de facture/devis par WhatsApp via l'API de techsoft-sms.com.

Aucune documentation publique n'a été trouvée pour l'envoi de document par
WhatsApp chez techsoft-sms.com au moment de l'implémentation (leur page
/api ne documente que l'envoi de SMS classique, et est signalée comme
abandonnée depuis décembre 2023). Les noms de paramètres ci-dessous sont
donc un point de départ par défaut, calqué sur :
  - le style de leur propre API SMS (requête GET, clé `api_key`,
    paramètres à plat) ;
  - la convention la plus répandue chez les passerelles WhatsApp pour
    joindre un document : une URL publique du fichier plutôt qu'un upload
    binaire (`document_url` / `url_media` selon les fournisseurs).

À corriger ici (noms de paramètres, méthode HTTP, endpoint exact) dès que
la vraie documentation du compte techsoft est disponible — le reste de
l'application (settings.TECHSOFT_*, apps.sales.views) n'a pas besoin de
changer, tout est centralisé dans ce module.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    """Levée si l'API n'est pas configurée ou si l'envoi échoue."""


def is_configured():
    return bool(settings.TECHSOFT_API_KEY)


def send_document(*, to, message, document_url, filename=None):
    """Envoie un message WhatsApp avec un document en pièce jointe.

    `to` : numéro déjà normalisé (chiffres uniquement, voir
    apps.sales.whatsapp.normalize_phone).
    `document_url` : URL publique du PDF (voir
    sales.views.invoice_public_pdf) — l'API techsoft va la récupérer
    elle-même, le fichier n'est jamais uploadé directement depuis Django.
    """

    if not is_configured():
        raise WhatsAppSendError("TECHSOFT_API_KEY n'est pas configurée (voir .env).")

    params = {
        "api_key": settings.TECHSOFT_API_KEY,
        "to": to,
        "message": message,
        "document_url": document_url,
    }
    if filename:
        params["filename"] = filename
    if settings.TECHSOFT_WHATSAPP_SENDER:
        params["from"] = settings.TECHSOFT_WHATSAPP_SENDER

    try:
        response = requests.get(settings.TECHSOFT_WHATSAPP_API_URL, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Échec de l'envoi WhatsApp (techsoft) vers %s : %s", to, exc)
        raise WhatsAppSendError(str(exc)) from exc

    logger.info("Message WhatsApp envoyé à %s (statut %s).", to, response.status_code)
    return response
