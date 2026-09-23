// Owner data is online-only. Never cache pages, photos, form submissions, or orders.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.mode !== 'navigate' || event.request.method !== 'GET' ||
      url.origin !== self.location.origin || !url.pathname.startsWith('/owner/')) return;
  event.respondWith(fetch(event.request, {cache: 'no-store'}).catch(() =>
    new Response('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Connection needed | CaCaCa Owner</title><body style="font:18px system-ui;padding:2rem;color:#082d3f"><h1>Connection needed</h1><p>Reconnect to the internet, then reopen CaCaCa Owner. Rods and orders stay on the server.</p></body></html>',
      {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}})
  ));
});
