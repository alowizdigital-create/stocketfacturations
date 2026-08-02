from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.stock.models import StockLevel, StockMovement


class Command(BaseCommand):
    help = "Recalcule tous les StockLevel à partir du registre StockMovement (filet de sécurité)."

    def handle(self, *args, **options):
        pairs = StockMovement.objects.values("boutique_id", "product_id").distinct()
        count = 0
        for pair in pairs:
            total = StockMovement.objects.filter(
                boutique_id=pair["boutique_id"], product_id=pair["product_id"]
            ).aggregate(total=Sum("quantity"))["total"] or 0
            StockLevel.objects.update_or_create(
                boutique_id=pair["boutique_id"],
                product_id=pair["product_id"],
                defaults={"quantity": total},
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"{count} niveaux de stock recalculés."))
