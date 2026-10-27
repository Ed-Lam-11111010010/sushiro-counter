const CACHE_NAME = 'sushiro-counter-v2';
const APP_SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icon.svg'
];

function isValidMenu(menu) {
  if (!menu || menu.schemaVersion !== 1 || !menu.categories || typeof menu.categories !== 'object') {
    return false;
  }
  if (menu.updatedAt !== undefined && (typeof menu.updatedAt !== 'string' || !Number.isFinite(Date.parse(menu.updatedAt)))) {
    return false;
  }
  const categories = Object.values(menu.categories);
  if (categories.length === 0 || categories.some(items => !Array.isArray(items) || items.length === 0)) {
    return false;
  }

  const ids = new Set();
  for (const item of categories.flat()) {
    if (!item || typeof item.id !== 'string' || !item.id.trim() || ids.has(item.id) ||
        typeof item.name !== 'string' || !item.name.trim() ||
        typeof item.price !== 'number' || !Number.isFinite(item.price) || item.price < 0) {
      return false;
    }
    ids.add(item.id);
  }
  return ids.size > 0;
}

async function isValidMenuResponse(response) {
  if (!response || !response.ok) return false;
  try {
    return isValidMenu(await response.clone().json());
  } catch (e) {
    return false;
  }
}

async function getValidCachedMenu() {
  const cached = await caches.match('./menu.json');
  return await isValidMenuResponse(cached) ? cached : undefined;
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(async () => {
        try {
          const response = await fetch('./menu.json', { cache: 'no-store' });
          if (await isValidMenuResponse(response)) {
            const cache = await caches.open(CACHE_NAME);
            await cache.put('./menu.json', response.clone());
          }
        } catch (e) { /* the app shell can still install if the menu is unavailable */ }
        return self.skipWaiting();
      })
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== self.location.origin) return;

  if (event.request.mode === 'navigate' || requestUrl.pathname.endsWith('/index.html')) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .then(response => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put('./index.html', copy));
          }
          return response;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }

  if (requestUrl.pathname.endsWith('/menu.json')) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' }).then(async response => {
          if (!(await isValidMenuResponse(response))) {
            return (await getValidCachedMenu()) || response;
          }
          try {
            const cache = await caches.open(CACHE_NAME);
            await cache.put('./menu.json', response.clone());
          } catch (e) { /* keep the valid network response if caching is unavailable */ }
          return response;
        })
        .catch(() => getValidCachedMenu())
    );
    return;
  }

  event.respondWith(
    caches.match(event.request)
      .then(cached => cached || fetch(event.request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        }
        return response;
      }))
  );
});
