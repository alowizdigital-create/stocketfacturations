from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.sync.activation import get_active_client
from apps.sync.outbox import run_push_cycle
from apps.sync.pull import run_pull_cycle


class Command(BaseCommand):
    help = (
        "Un cycle pull puis push contre SYNC_BASE_URL, via le jeton "
        "d'activation local de ce poste. Poste offline uniquement — "
        "c'est le point d'entrée qu'appellera plus tard le worker de "
        "synchro en tâche de fond (desktop/sync_worker.py, pas encore "
        "construit) dans une boucle."
    )

    def handle(self, *args, **options):
        if not settings.IS_OFFLINE:
            raise CommandError("sync_now ne s'exécute qu'avec config.settings.offline.")

        try:
            client = get_active_client()
        except RuntimeError as exc:
            raise CommandError(str(exc))

        self.stdout.write("Pull...")
        pull_summary = run_pull_cycle(client)
        for resource, count in pull_summary.items():
            self.stdout.write(f"  {resource}: {count}")

        self.stdout.write("Push...")
        push_summary = run_push_cycle(client)
        for kind, result in push_summary.items():
            self.stdout.write(f"  {kind}: {result}")

        self.stdout.write(self.style.SUCCESS("Synchronisation terminée."))
