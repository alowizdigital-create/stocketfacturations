# Déploiement en ligne — VPS Hostinger + Docker + zweey.com

Ce document décrit comment mettre en ligne le premier déploiement de l'application
sur un VPS Hostinger, avec Docker Compose et le nom de domaine `zweey.com`.

Le déploiement utilise 3 conteneurs :
- **db** — PostgreSQL 16
- **web** — l'application Django (gunicorn), migrations + fichiers statiques appliqués automatiquement au démarrage
- **caddy** — reverse proxy + **HTTPS automatique** (certificat Let's Encrypt obtenu et renouvelé tout seul, aucune manipulation certbot à faire)

---

## 0. Prérequis

- Le VPS Hostinger est accessible en SSH.
- Le domaine `zweey.com` est enregistré et vous pouvez modifier sa zone DNS.
- Les ports **80** et **443** doivent être libres et ouverts sur le VPS (nécessaires à Caddy pour obtenir le certificat HTTPS).

---

## 1. Pointer le domaine vers le VPS

Dans la zone DNS de `zweey.com` (panneau Hostinger ou votre registrar), créer :

| Type | Nom | Valeur                  |
|------|-----|--------------------------|
| A    | @   | \<IP publique du VPS\>  |
| A    | www | \<IP publique du VPS\>  |

La propagation DNS peut prendre de quelques minutes à quelques heures. Vous pouvez
vérifier avec `nslookup zweey.com` avant de continuer.

---

## 2. Installer Docker sur le VPS (si pas déjà fait)

Connectez-vous en SSH au VPS, puis :

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Déconnectez-vous et reconnectez-vous pour que l'appartenance au groupe `docker`
prenne effet. Vérifiez :

```bash
docker --version
docker compose version
```

Ouvrez les ports nécessaires (si `ufw` est actif) :

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

---

## 3. Récupérer le code sur le VPS

```bash
git clone https://github.com/alowizdigital-create/stocketfacturations.git stock-facturation
cd stock-facturation
```

(Ou `git pull` si le dossier existe déjà sur le VPS.)

---

## 4. Configurer les variables d'environnement

```bash
cp .env.example .env
nano .env
```

À remplir dans `.env` :

- `DJANGO_SECRET_KEY` — une vraie clé secrète aléatoire, **jamais** la valeur d'exemple. Pour en générer une :
  ```bash
  docker run --rm python:3.14-slim python -c "import secrets; print(secrets.token_urlsafe(50))"
  ```
- `POSTGRES_PASSWORD` — un mot de passe fort (le mot de passe de la base de données).
- Les autres valeurs (`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, etc.) sont déjà correctes pour `zweey.com` par défaut — à ajuster seulement si besoin.

Le fichier `.env` ne doit **jamais** être commité (il est déjà exclu par `.gitignore`).

Si votre adresse email diffère, changez aussi `email admin@zweey.com` dans
`docker/Caddyfile` (utilisée uniquement par Let's Encrypt pour les notifications
d'expiration de certificat).

---

## 5. Construire et démarrer

```bash
docker compose build
docker compose up -d
```

Au démarrage, le conteneur `web` applique automatiquement les migrations et
collecte les fichiers statiques (voir `docker/entrypoint.sh`) avant de lancer
gunicorn — aucune commande manuelle n'est nécessaire pour un premier déploiement.

Suivre les logs pendant les premières minutes :

```bash
docker compose logs -f
```

Dans les logs de `caddy`, vous devez voir l'obtention du certificat TLS pour
`zweey.com` (cherchez `certificate obtained successfully`). Cela nécessite que le
DNS pointe déjà vers le VPS (étape 1) et que les ports 80/443 soient accessibles
depuis internet.

---

## 6. Vérifier

- Ouvrir `https://zweey.com` — la page de connexion doit s'afficher, avec un
  cadenas HTTPS valide.
- Créer la première entreprise via `https://zweey.com/entreprises/inscription/`
  (crée automatiquement le compte admin, la boutique par défaut et une unité de
  mesure de base).
- Optionnel — créer aussi un compte superutilisateur Django (utile pour le
  support/l'administration via `/admin/`) :
  ```bash
  docker compose exec web python manage.py createsuperuser
  ```

---

## 7. Mises à jour ultérieures

```bash
git pull
docker compose build
docker compose up -d
```

Les migrations et le `collectstatic` s'appliquent automatiquement à chaque
redémarrage du conteneur `web`.

---

## 8. Sauvegarde de la base de données

```bash
docker compose exec db pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup_$(date +%Y%m%d).sql
```

À planifier régulièrement (cron sur le VPS), et à copier hors du serveur.

---

## Notes

- Les certificats TLS et les données Postgres/médias sont stockés dans des
  volumes Docker nommés (`caddy_data`, `postgres_data`, `media_data`,
  `static_data`) — ils survivent aux `docker compose build`/`up`, mais seraient
  perdus avec un `docker compose down -v`. **Ne jamais utiliser `-v` en
  production** sauf intention explicite de tout réinitialiser.
- Ce dépôt contenait un ancien `Dockerfile`/`docker-compose.yml` copiés depuis un
  autre projet (référençant `deploy/entrypoint.sh` et un `requirements.txt` à
  plat, inexistants ici) — ils ont été remplacés par les fichiers actuels,
  adaptés à la structure réelle du projet (`requirements/online.txt`,
  `docker/entrypoint.sh`, `config.settings.online`).
- La synchronisation avec la future version offline (.exe) et l'API de synchro
  (`api.zweey.com`) sont prévues dans une phase ultérieure — non nécessaires
  pour ce premier déploiement.
