from decimal import Decimal

import factory

from apps.accounts.models import User
from apps.catalog.models import Product, Unit
from apps.sales.models import Client
from apps.tenants.models import BoutiqueAPIToken, Boutique, Compte, Membership


class CompteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Compte

    name = factory.Sequence(lambda n: f"Entreprise Test {n}")


class BoutiqueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Boutique

    compte = factory.SubFactory(CompteFactory)
    name = factory.Sequence(lambda n: f"Boutique Test {n}")
    code = factory.Sequence(lambda n: f"BTQ{n:03d}")
    devise = "XOF"


class UnitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Unit

    compte = factory.SubFactory(CompteFactory)
    name = "Pièce"
    symbol = "pc"


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    compte = factory.SubFactory(CompteFactory)
    unit = factory.SubFactory(UnitFactory, compte=factory.SelfAttribute("..compte"))
    name = factory.Sequence(lambda n: f"Produit Test {n}")
    default_sale_price = Decimal("1000")
    tva_rate = Decimal("18")


class ClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Client

    boutique = factory.SubFactory(BoutiqueFactory)
    name = factory.Sequence(lambda n: f"Client Test {n}")


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@test.local")
    first_name = "Test"
    last_name = "User"

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        obj.set_password(extracted or "testpass123")
        if create:
            obj.save()


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    boutique = factory.SubFactory(BoutiqueFactory)
    role = Membership.CAISSIER


def issue_token(boutique):
    """Émet un jeton API pour la boutique et renvoie `(token, raw_token)` —
    même mécanisme que le bouton "Générer un jeton" des paramètres."""
    return BoutiqueAPIToken.issue(boutique)
