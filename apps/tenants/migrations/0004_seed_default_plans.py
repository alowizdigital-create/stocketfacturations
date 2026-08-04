from django.db import migrations

DEFAULT_PLANS = [
    {
        "name": "Gratuit",
        "price_monthly": 0,
        "max_boutiques": 1,
        "max_users": 2,
        "description": "1 boutique, 2 employés.",
    },
    {
        "name": "Standard",
        "price_monthly": 15000,
        "max_boutiques": 3,
        "max_users": 10,
        "description": "3 boutiques, 10 employés.",
    },
    {
        "name": "Illimité",
        "price_monthly": 30000,
        "max_boutiques": None,
        "max_users": None,
        "description": "Boutiques et employés illimités.",
    },
]


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("tenants", "Plan")
    Subscription = apps.get_model("tenants", "Subscription")
    Compte = apps.get_model("tenants", "Compte")
    import uuid
    from django.utils import timezone

    plans = {}
    for data in DEFAULT_PLANS:
        plan, _ = Plan.objects.get_or_create(name=data["name"], defaults={**data, "id": uuid.uuid4()})
        plans[data["name"]] = plan

    # Abonne les entreprises déjà existantes (créées avant ce module) au
    # plan gratuit par défaut, pour qu'aucun Compte ne se retrouve sans
    # Subscription.
    free_plan = plans["Gratuit"]
    for compte in Compte.objects.filter(subscription__isnull=True):
        Subscription.objects.create(
            id=uuid.uuid4(), compte=compte, plan=free_plan, started_at=timezone.localdate()
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0003_plan_remove_compte_plan_subscription"),
    ]

    operations = [
        migrations.RunPython(seed_plans, noop),
    ]
