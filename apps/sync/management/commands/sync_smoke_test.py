import uuid

import requests
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Vérification bout-en-bout de l'API de synchronisation contre un "
        "serveur réel : ping -> pull catalogue -> push d'un bundle vente -> "
        "rejeu du même bundle (idempotence). Nécessite un BoutiqueAPIToken "
        "valide (généré depuis les paramètres boutique)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True, help="Ex: http://127.0.0.1:8020")
        parser.add_argument("--token", required=True, help="Jeton boutique en clair")

    def handle(self, *args, **options):
        base_url = options["base_url"].rstrip("/")
        headers = {"Authorization": f"Bearer {options['token']}"}

        self.stdout.write("1. ping...")
        resp = requests.get(f"{base_url}/api/v1/sync/ping/", headers=headers, timeout=10)
        if resp.status_code != 200:
            raise CommandError(f"ping a échoué ({resp.status_code}): {resp.text}")
        boutique = resp.json()["boutique"]
        self.stdout.write(self.style.SUCCESS(f"   OK — boutique {boutique['name']} ({boutique['id']})"))

        self.stdout.write("2. pull catalogue produits...")
        resp = requests.get(f"{base_url}/api/v1/sync/pull/catalog/products/", headers=headers, timeout=10)
        if resp.status_code != 200:
            raise CommandError(f"pull produits a échoué ({resp.status_code}): {resp.text}")
        products = resp.json()["results"]
        self.stdout.write(self.style.SUCCESS(f"   OK — {len(products)} produit(s) reçus"))

        product_id = products[0]["id"] if products else None
        sale_id = str(uuid.uuid4())
        item = {
            "id": sale_id,
            "lines": [{
                "description": "Article de test (sync_smoke_test)",
                "quantity": "1",
                "unit_price_ht": "1000",
                "tva_rate": "0",
                **({"product_id": product_id, "stock_movement_id": str(uuid.uuid4())} if product_id else {}),
            }],
            "generate_invoice": True,
            "invoice_id": str(uuid.uuid4()),
        }
        payload = {"items": [item]}

        self.stdout.write("3. push du bundle vente...")
        key = str(uuid.uuid4())
        resp = requests.post(
            f"{base_url}/api/v1/sync/push/sale-transactions/", json=payload,
            headers={**headers, "Idempotency-Key": key}, timeout=10,
        )
        if resp.status_code != 200:
            raise CommandError(f"push a échoué ({resp.status_code}): {resp.text}")
        result = resp.json()["results"][0]
        if result["status"] != "created":
            raise CommandError(f"statut inattendu au premier push : {result}")
        self.stdout.write(self.style.SUCCESS(f"   OK — vente {sale_id} créée, facture {result['invoice_id']}"))

        self.stdout.write("4. rejeu du même bundle, même Idempotency-Key (doit être un no-op identique)...")
        replay = requests.post(
            f"{base_url}/api/v1/sync/push/sale-transactions/", json=payload,
            headers={**headers, "Idempotency-Key": key}, timeout=10,
        )
        if replay.json() != resp.json():
            raise CommandError("le rejeu avec la même clé n'a pas renvoyé une réponse identique.")
        self.stdout.write(self.style.SUCCESS("   OK — réponse identique en cache"))

        self.stdout.write("5. rejeu du même id de vente, nouvelle clé (doit être signalé 'duplicate')...")
        replay2 = requests.post(
            f"{base_url}/api/v1/sync/push/sale-transactions/", json=payload,
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())}, timeout=10,
        )
        result2 = replay2.json()["results"][0]
        if result2["status"] != "duplicate":
            raise CommandError(f"statut inattendu au rejeu (id existant, clé différente) : {result2}")
        self.stdout.write(self.style.SUCCESS("   OK — signalé comme doublon, aucune nouvelle vente créée"))

        self.stdout.write(self.style.SUCCESS("Smoke test de synchronisation : tout est vert."))
