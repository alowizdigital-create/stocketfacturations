from .models import Notification


def notify(user, message, *, url=""):
    """Crée une notification en app pour `user` — voir Notification et
    apps.core.context_processors.notifications pour l'affichage (cloche
    dans la barre du haut)."""
    return Notification.objects.create(user=user, message=message, url=url)
