// Service worker minimal — sert uniquement à rendre l'application
// installable (critère requis par Chrome/Android). Pas de cache hors
// ligne volontairement : les données de stock/facturation changent en
// permanence, mieux vaut toujours repasser par le réseau pour l'instant.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
