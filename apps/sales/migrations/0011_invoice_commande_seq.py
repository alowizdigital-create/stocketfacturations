from django.db import migrations, models


def number_existing_commandes(apps, schema_editor):
    """Numérote les commandes déjà créées (1, 2, 3...) par boutique, dans
    l'ordre de création — annulées comprises, comme pour les nouvelles."""
    Invoice = apps.get_model("sales", "Invoice")
    boutique_ids = (
        Invoice.objects.filter(type="COMMANDE").values_list("boutique_id", flat=True).distinct()
    )
    for boutique_id in boutique_ids:
        commandes = Invoice.objects.filter(boutique_id=boutique_id, type="COMMANDE").order_by("created_at", "pk")
        for seq, commande in enumerate(commandes, start=1):
            Invoice.objects.filter(pk=commande.pk).update(commande_seq=seq)


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0010_paymentreminder'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='commande_seq',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='n° de commande'),
        ),
        migrations.RunPython(number_existing_commandes, migrations.RunPython.noop),
    ]
