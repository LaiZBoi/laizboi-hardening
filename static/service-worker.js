const CACHE_NAME = 'clientst0r-v4';
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png'
];

// Install: cache only static assets (never HTML pages)
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames =>
      Promise.all(
        cacheNames
          .filter(name => name !== CACHE_NAME)
          .map(name => caches.delete(name))
      )
    )
  );
});

// Fetch strategy:
//   - Navigation requests (HTML pages): network-first, never cache.
//     This ensures Django's auth/session checks always run.
//   - Static assets: cache-first with network fallback.
self.addEventListener('fetch', event => {
  const { request } = event;

  // Always go to the network for navigation (page loads) so session
  // expiry is enforced server-side on every page visit.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        // Offline fallback: serve cached root if available, otherwise a
        // plain error page so the user knows they are offline.
        caches.match('/').then(
          cached => cached || new Response(
            '<h1>You are offline</h1><p>Please check your connection and try again.</p>',
            { headers: { 'Content-Type': 'text/html' } }
          )
        )
      )
    );
    return;
  }

  // Static assets: cache-first
  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request))
  );
});
