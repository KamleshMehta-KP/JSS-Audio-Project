// JSS Audio Library Service Worker
// Background refresh with selective caching for important file types

const CACHE_NAME = "jss-audio-cache-v1";

// App shell files (homepage + icons)
const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

// Install: cache the app shell
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))
    )
  );
  self.clients.claim();
});

// Fetch: cache only selected file types, auto-refresh in background
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  // Cache HTML, audio, images, CSS, JS
  const isTargetFile =
    url.pathname.endsWith(".html") ||
    url.pathname.endsWith(".mp3") ||
    url.pathname.endsWith(".png") ||
    url.pathname.endsWith(".css") ||
    url.pathname.endsWith(".js");

  if (!isTargetFile) {
    // For other files, just fetch normally
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(cache =>
      cache.match(event.request).then(cachedResponse => {
        const fetchPromise = fetch(event.request).then(networkResponse => {
          if (networkResponse && networkResponse.status === 200) {
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        }).catch(() => cachedResponse);

        // Show cached immediately, update quietly in background
        return cachedResponse || fetchPromise;
      })
    )
  );
});
