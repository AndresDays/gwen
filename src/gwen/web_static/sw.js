const CACHE = 'gwen-shell-v6';
const ASSETS = [
  '/static/styles.css?v=6',
  '/static/app.js?v=6',
  '/static/icon-192.png',
  '/static/icon-512.png'
];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin || !url.pathname.startsWith('/static/')) return;
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});