/* Voxlyra comics shell service worker.
   It caches only the reader shell and static interface files. Signed page URLs
   are intentionally left to the encrypted/local IndexedDB cache in comic.js. */
const SHELL_CACHE = "voxlyra-comic-shell-v197";
const STATIC_CACHE = "voxlyra-comic-static-v197";
const CORE_FILES = [
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/comic.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(CORE_FILES))
      .catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(
      names
        .filter((name) => name.startsWith("voxlyra-comic-") && ![SHELL_CACHE, STATIC_CACHE].includes(name))
        .map((name) => caches.delete(name))
    ))
  );
  self.clients.claim();
});

function isComicShell(url) {
  return url.origin === self.location.origin && /^\/comic\/\d+\/?$/.test(url.pathname);
}

function isStaticFile(url) {
  return url.origin === self.location.origin && url.pathname.startsWith("/static/");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (isComicShell(url)) {
    event.respondWith((async () => {
      const cache = await caches.open(SHELL_CACHE);
      try {
        const fresh = await fetch(request);
        if (fresh.ok) await cache.put(request, fresh.clone());
        return fresh;
      } catch (_) {
        const cached = await cache.match(request, { ignoreSearch: true });
        return cached || new Response(
          "Страница ещё не была сохранена на устройстве.",
          { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } }
        );
      }
    })());
    return;
  }

  if (isStaticFile(url)) {
    event.respondWith((async () => {
      const cached = await caches.match(request, { ignoreSearch: true });
      if (cached) return cached;
      try {
        const fresh = await fetch(request);
        if (fresh.ok) {
          const cache = await caches.open(STATIC_CACHE);
          await cache.put(request, fresh.clone());
        }
        return fresh;
      } catch (_) {
        return new Response("", { status: 503 });
      }
    })());
  }
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "CACHE_COMIC_SHELL" || !Array.isArray(data.urls)) return;
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    for (const rawUrl of data.urls) {
      try {
        const url = new URL(rawUrl, self.location.origin);
        if (!isComicShell(url)) continue;
        const response = await fetch(url.toString(), { credentials: "same-origin" });
        if (response.ok) await cache.put(url.pathname, response.clone());
      } catch (_) {
        // One unavailable chapter must not abort caching the rest of the volume.
      }
    }
  })());
});
