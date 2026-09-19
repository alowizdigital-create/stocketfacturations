from django.conf import settings


def is_offline(request):
    """Expose IS_OFFLINE aux templates — utile pour adapter des comportements
    qui ne se justifient qu'en ligne (ex: ouvrir un lien dans un nouvel
    onglet) car hors-ligne la fenêtre pywebview de l'exe n'a ni onglets ni
    navigateur système partageant la session de l'app."""
    return {"IS_OFFLINE": settings.IS_OFFLINE}


def notifications(request):
    """Notifications non lues de l'utilisateur courant, pour la cloche de
    la barre du haut (voir templates/base.html) — consultées à chaque
    navigation, pas de push temps réel (voir apps.core.models.Notification)."""
    if not request.user.is_authenticated:
        return {}
    from .models import Notification

    unread = Notification.objects.filter(user=request.user, is_read=False)
    return {
        "unread_notifications": unread[:10],
        "unread_notifications_count": unread.count(),
    }
