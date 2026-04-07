/* MAXIA PWA Service Worker v13.0 */
const CACHE_NAME = 'maxia-v13.0';
const PRECACHE = ['/offline.html', '/manifest.json', '/favicon.svg', '/static/icon-192.png', '/static/icon-512.png'];

self.addEventListener('install', function(e) {
  e.waitUntil(caches.open(CACHE_NAME).then(function(c) { return c.addAll(PRECACHE); }));
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k) { return k !== CACHE_NAME; }).map(function(k) { return caches.delete(k); }));
    }).then(function() { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function(e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  /* Ne pas cacher les API, WebSocket, extensions */
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws') || url.protocol === 'chrome-extension:') return;

  /* Navigation (pages HTML) — network-first, fallback offline.html */
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then(function(r) {
        if (r.ok) { var cl = r.clone(); caches.open(CACHE_NAME).then(function(c) { c.put(req, cl); }); }
        return r;
      }).catch(function() {
        return caches.match(req).then(function(cached) { return cached || caches.match('/offline.html'); });
      })
    );
    return;
  }

  /* Assets statiques — stale-while-revalidate */
  e.respondWith(
    caches.match(req).then(function(cached) {
      var fetchPromise = fetch(req).then(function(r) {
        if (r.ok) { var cl = r.clone(); caches.open(CACHE_NAME).then(function(c) { c.put(req, cl); }); }
        return r;
      }).catch(function() { return cached; });
      return cached || fetchPromise;
    })
  );
});
