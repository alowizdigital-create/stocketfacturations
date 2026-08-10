import requests


class SyncClient:
    """Client HTTP pour l'API de synchro du serveur (voir apps.sync.views),
    utilisé côté poste offline pour pousser/tirer des données."""

    def __init__(self, base_url, token, timeout=15):
        self.base_url = base_url.rstrip("/") + "/api/v1/sync/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.base_url + path.lstrip("/")

    def get(self, path, params=None):
        resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def post(self, path, json_body, headers=None):
        resp = self.session.post(self._url(path), json=json_body, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def ping(self):
        return self.get("ping/")
