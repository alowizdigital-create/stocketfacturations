"""
Worker de synchronisation en tâche de fond pour le poste offline — appelle
en boucle les cycles pull/push déjà construits et testés dans apps.sync
(apps/sync/pull.py, apps/sync/outbox.py). Ne dépend d'aucun code
PyInstaller-spécifique : ce module tourne aussi bien en dev
(config.settings.offline non empaqueté) que dans l'exe.
"""

import logging
import threading

import requests
from django.conf import settings

log = logging.getLogger("desktop.sync_worker")

DEFAULT_INTERVAL = 60  # secondes entre deux cycles automatiques


class SyncWorker:
    """Thread démon : tant que non activé, attend simplement le tour
    suivant (aucune erreur, aucun log bruyant) ; une fois activé, tire
    puis pousse à intervalle régulier. `wake_event` permet de déclencher
    un cycle immédiat sans attendre l'intervalle — non câblé à une UI
    dans cet incrément, prêt pour un futur bouton "Synchroniser
    maintenant" sans retoucher ce fichier."""

    def __init__(self, interval=DEFAULT_INTERVAL):
        self.interval = interval
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="sync-worker", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()  # débloque immédiatement un éventuel wait()

    def _run(self):
        # Import différé : apps.sync dépend des modèles Django, qui ne
        # sont prêts qu'après django.setup() (fait par le lanceur avant
        # de démarrer ce thread) — importer en tête de module casserait
        # l'ordre d'initialisation si ce fichier est un jour importé plus tôt.
        from apps.sync.activation import get_active_client
        from apps.sync.outbox import run_push_cycle
        from apps.sync.pull import run_pull_cycle

        if not settings.IS_OFFLINE:
            log.warning("SyncWorker démarré alors que IS_OFFLINE=False — arrêt immédiat.")
            return

        log.info("Worker de synchro démarré (intervalle=%ss).", self.interval)
        while not self.stop_event.is_set():
            try:
                client = get_active_client()
            except RuntimeError:
                # Poste pas encore activé : rien à synchroniser, on
                # patiente simplement jusqu'au tour suivant.
                pass
            else:
                # Pull et push dans des try/except SÉPARÉS, volontairement :
                # un échec du pull (ex: une ressource en erreur qui remonte
                # malgré la résilience par ressource/item d'apps.sync.pull)
                # ne doit jamais empêcher le push de tourner — sinon les
                # écritures locales (produits, ventes...) ne partiraient
                # plus jamais tant que le pull resterait en échec.
                try:
                    run_pull_cycle(client)
                except requests.RequestException as exc:
                    log.info("Pull impossible pour l'instant (réseau) : %s", exc)
                except Exception:  # noqa: BLE001 — ce thread ne doit jamais mourir
                    log.exception("Erreur inattendue pendant le pull.")

                try:
                    run_push_cycle(client)
                except requests.RequestException as exc:
                    log.info("Push impossible pour l'instant (réseau) : %s", exc)
                except Exception:  # noqa: BLE001 — ce thread ne doit jamais mourir
                    log.exception("Erreur inattendue pendant le push.")

            self.wake_event.wait(timeout=self.interval)
            self.wake_event.clear()

        log.info("Worker de synchro arrêté.")
