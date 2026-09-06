// JSS Audio Library Service Worker  (jss-patch-v1)
// Stale-while-revalidate for pages, cache-first for fonts, with a size cap
// so a 12 MB library doesn't grow without bound.

const CACHE_NAME = "jss-audio-cache-v3";
const FONT_CACHE = "jss-fonts-v1";
const MAX_ENTRIES = 60;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      // addAll is all-or-nothing; a single 404 used to abort the whole install.
      Promise.allSettled(SHELL_FILES.map(f => cache.add(f)))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME && k !== FONT_CACHE).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= max) return;
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
}

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Google Fonts: cache-first so Gujarati and Hindi still render offline.
  // The old worker skipped them because they carry no file extension.
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(
      caches.open(FONT_CACHE).then(cache =>
        cache.match(req).then(hit =>
          hit || fetch(req).then(res => {
            if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone());
            return res;
          }).catch(() => hit)
        )
      )
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  const p = url.pathname;
  const isTarget =
    p.endsWith(".html") || p.endsWith("/") ||
    p.endsWith(".mp3") || p.endsWith(".vtt") ||
    p.endsWith(".png") || p.endsWith(".css") ||
    p.endsWith(".js")  || p.endsWith(".json");
  if (!isTarget) return;

  event.respondWith(
    caches.open(CACHE_NAME).then(cache =>
      cache.match(req).then(cached => {
        const network = fetch(req).then(res => {
          if (res && res.status === 200) {
            cache.put(req, res.clone()).then(() => trim(CACHE_NAME, MAX_ENTRIES));
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    )
  );
});

self.addEventListener("message", e => {
  if (e.data === "skip-waiting") self.skipWaiting();
});
