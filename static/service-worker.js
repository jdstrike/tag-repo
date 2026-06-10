// Service worker: caches the app shell + static assets for offline experience
const CACHE_NAME = 'utm-tool-v1.0.0';
const SHELL = [
  '/', '/static/qrcode.min.js',
  '/static/pwa/pwa-192.png', '/static/pwa/pwa-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  // Network-first for HTML, cache-first for static assets
  const url = new URL(e.request.url);
  // Don't intercept API or short-link redirects
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/s/') || url.pathname.startsWith('/admin/')) {
    return;
  }
  if (e.request.destination === 'document') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request).then((r) => r || caches.match('/')))
    );
  } else if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then((r) =>
        r ||
        fetch(e.request).then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(e.request, copy));
          return resp;
        })
      )
    );
  }
});
