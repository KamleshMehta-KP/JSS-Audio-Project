// Bump this version number any time you want returning visitors
// to pick up a fresh copy of the homepage / icons (e.g. after a logo fix).
const CACHE_NAME = "jss-audio-cache-v2";

// Only the "app shell" is cached here — the homepage and its icons.
// Individual book/audio pages are NOT listed here on purpose:
// they are fetched fresh from the network and cached automatically
// as visitors open them (see the fetch handler below), so you never
// need to edit this file when you add a new book.
const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  // Delete old-versioned caches so updates actually take effect
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(cachedResponse => {
      if (cachedResponse) {
        return cachedResponse;
      }
      // Not in cache (e.g. a book page or audio file) — fetch from
      // network, then store a copy for offline use next time.
      return fetch(event.request).then(networkResponse => {
        // Only cache successful, same-origin responses
        if (
          networkResponse &&
          networkResponse.status === 200 &&
          event.request.url.startsWith(self.location.origin)
        ) {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseClone);
          });
        }
        return networkResponse;
      }).catch(() => {
        // Offline and not cached - nothing more we can do
        return cachedResponse;
      });
    })
  );
});
