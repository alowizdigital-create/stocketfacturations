"""
Point d'entrée de l'exe Windows (PyInstaller) : démarre Django (SQLite
locale), le serveur waitress, le worker de synchro en tâche de fond, puis
ouvre la fenêtre desktop (pywebview). Aucune dépendance à `manage.py` —
tout est piloté programmatiquement pour tourner sans console ni terminal.

Variable d'environnement `STOCKFACT_NO_GUI=1` : ne pas ouvrir la fenêtre,
rester bloqué en simple serveur HTTP — c'est l'échappatoire utilisée pour
vérifier la pile Django/waitress d'un build sans interaction graphique.
"""

import logging
import os
import socket
import sys
from pathlib import Path


def _bootstrap_logging():
    """Filet de sécurité posé AVANT tout le reste : en mode fenêtré sans
    console, une exception pendant django.setup()/migrate serait sinon
    invisible et le processus disparaîtrait sans aucune trace."""

    if getattr(sys, "frozen", False):
        import platformdirs

        data_dir = Path(platformdirs.user_data_dir("StockFacturation", "Zweey"))
    else:
        data_dir = Path(__file__).resolve().parent.parent / "var" / "offline"
    data_dir.mkdir(parents=True, exist_ok=True)

    log_file = data_dir / "launcher.log"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def _log_uncaught(exc_type, exc_value, exc_tb):
        logging.critical("Exception non interceptée", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _log_uncaught
    return data_dir, log_file


def _show_fatal_error(message):
    """Affiche une erreur à l'utilisateur même si la fenêtre principale
    n'a jamais pu s'ouvrir — pas de console disponible pour un print()."""
    logging.critical(message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Stock & Facturation — Erreur", 0x10)
    except Exception:  # noqa: BLE001 — dernier recours, ne doit jamais lever
        pass


def _resolve_port(preferred=8765):
    """Port fixe si disponible (pratique, stable) ; sinon un port libre
    découvert dynamiquement — ne bloque jamais le démarrage pour cette
    raison (ex: une instance précédente mal fermée occupe encore le port)."""

    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                bound_port = s.getsockname()[1]
            return bound_port
        except OSError:
            continue
    raise RuntimeError("Impossible de trouver un port libre.")


def main():
    data_dir, log_file = _bootstrap_logging()
    log = logging.getLogger("desktop.launcher")
    log.info("Démarrage — logs dans %s", log_file)

    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.offline"

    try:
        import django

        django.setup()
    except Exception:
        log.exception("Échec de django.setup()")
        _show_fatal_error("L'application n'a pas pu démarrer (initialisation Django).")
        sys.exit(1)

    try:
        from django.core.management import call_command

        # Idempotent et rapide (no-op si déjà à jour) — exécuté à chaque
        # lancement pour ne jamais laisser une base locale en retard
        # après une mise à jour de l'app.
        call_command("migrate", interactive=False, verbosity=0)
    except Exception:
        log.exception("Échec de la migration de la base locale")
        _show_fatal_error(
            "La base de données locale n'a pas pu être mise à jour. "
            "Consultez le journal pour plus de détails :\n" + str(data_dir / "app.log")
        )
        sys.exit(1)

    from django.core.wsgi import get_wsgi_application

    application = get_wsgi_application()

    port = _resolve_port()
    log.info("Port résolu : %s", port)

    import waitress

    server = waitress.create_server(application, host="127.0.0.1", port=port, threads=4)

    import threading

    server_thread = threading.Thread(target=server.run, name="waitress", daemon=True)
    server_thread.start()

    from desktop.sync_worker import SyncWorker

    sync_worker = SyncWorker()
    sync_worker.start()

    url = f"http://127.0.0.1:{port}/"

    if os.environ.get("STOCKFACT_NO_GUI") == "1":
        # Échappatoire de vérification (pas de GUI dans cet environnement
        # de build) : reste bloqué en serveur HTTP nu jusqu'à Ctrl+C/kill.
        log.info("STOCKFACT_NO_GUI=1 — pas de fenêtre, serveur seul sur %s", url)
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            sync_worker.stop()
            server.close()
        return

    import webview

    icon_path = None
    if getattr(sys, "frozen", False):
        candidate = Path(sys._MEIPASS) / "app.ico"  # noqa: SLF001
        if candidate.exists():
            icon_path = str(candidate)
    else:
        candidate = Path(__file__).resolve().parent.parent / "pyinstaller" / "app.ico"
        if candidate.exists():
            icon_path = str(candidate)

    window = webview.create_window("Stock & Facturation", url, width=1280, height=800)

    def _on_closing():
        log.info("Fenêtre fermée — arrêt propre du worker et du serveur.")
        sync_worker.stop()
        server.close()

    window.events.closing += _on_closing

    webview.start(gui="edgechromium", icon=icon_path)


if __name__ == "__main__":
    main()
