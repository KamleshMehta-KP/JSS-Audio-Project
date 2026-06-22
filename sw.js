self.addEventListener("install", event => {
  event.waitUntil(
    caches.open("jss-audio-cache").then(cache => {
      return cache.addAll([
        "/",
        "/index.html",
        "/AkramVignan-Gujarati/AVG_2026_04_05_April_May_V3_Guj_210626.html",
        "/audio/file1.mp3",
        "/audio/file2.mp3"
      ]);
    })
  );
});

self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(response => {
      return response || fetch(event.request);
    })
  );
});
