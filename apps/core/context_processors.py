from django.conf import settings


def is_offline(request):
    """Expose IS_OFFLINE aux templates — utile pour adapter des comportements
    qui ne se justifient qu'en ligne (ex: ouvrir un lien dans un nouvel
    onglet) car hors-ligne la fenêtre pywebview de l'exe n'a ni onglets ni
    navigateur système partageant la session de l'app."""
    return {"IS_OFFLINE": settings.IS_OFFLINE}
