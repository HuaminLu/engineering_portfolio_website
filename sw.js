/* Service worker for the portfolio — OPT-IN.
 *
 * It is registered only from offline.html (see js/offline.js), so ordinary
 * visitors never get it. Once a browser has it, the whole site works offline:
 *   - pages, CSS, JS and JSON are NETWORK-FIRST: online you always see the live
 *     version (a deploy shows up immediately), offline the cached copy is used
 *   - images, videos, fonts and the demo's CDN libraries are CACHE-FIRST
 *   - "Save for offline" on offline.html fills the cache with every page and
 *     media file; cached videos are served with proper 206 range responses
 *   - analytics requests (script.google.com, ipwho.is) are never intercepted
 * To retire it, deploy a version whose activate handler unregisters itself.
 */
var CACHE = 'portfolio-v1';
var RUNTIME_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com'];

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) {
        return k.indexOf('portfolio-') === 0 && k !== CACHE;
      }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  var same = url.origin === self.location.origin;
  if (!same && RUNTIME_HOSTS.indexOf(url.hostname) === -1) return; // analytics etc. untouched
  var isCode = same && (req.mode === 'navigate' || /\.(html|css|js|json)$/.test(url.pathname) || url.pathname.slice(-1) === '/');
  e.respondWith(isCode ? networkFirst(req) : cacheFirst(req));
});

// Cache entries are keyed by plain URL (no Range header), which is also how
// offline.js stores them when saving the whole site.
function cacheKey(req) { return new Request(req.url); }

function networkFirst(req) {
  return caches.open(CACHE).then(function (cache) {
    return fetch(req).then(function (res) {
      if (res && res.ok) cache.put(cacheKey(req), res.clone());
      return res;
    }).catch(function () {
      return cache.match(req, { ignoreSearch: true }).then(function (hit) {
        if (hit) return hit;
        if (req.mode !== 'navigate') throw new Error('offline and not cached: ' + req.url);
        var u = new URL(req.url);
        var alt = u.pathname.slice(-1) === '/' ? new URL('index.html', u).href : null;
        return (alt ? cache.match(alt) : Promise.resolve(null)).then(function (h2) {
          return h2 || offlinePage();
        });
      });
    });
  });
}

function cacheFirst(req) {
  return caches.open(CACHE).then(function (cache) {
    return cache.match(cacheKey(req)).then(function (hit) {
      if (hit) return req.headers.has('range') ? rangeFrom(hit, req.headers.get('range')) : hit;
      return fetch(req).then(function (res) {
        // Only complete responses are stored (200, or opaque for CDN/no-cors).
        // Ranged 206 replies for videos are passed through untouched.
        if (res && (res.status === 200 || res.type === 'opaque') && !req.headers.has('range')) {
          cache.put(cacheKey(req), res.clone());
        }
        return res;
      });
    });
  });
}

// Serve a byte range out of a fully cached media file (what <video> asks for).
function rangeFrom(res, rangeHeader) {
  return res.blob().then(function (blob) {
    var total = blob.size;
    var m = /bytes=(\d*)-(\d*)/.exec(rangeHeader || '');
    if (!m || total === 0) return new Response(blob, { status: 200, headers: res.headers });
    var start, end;
    if (m[1]) {
      start = parseInt(m[1], 10);
      end = m[2] ? Math.min(parseInt(m[2], 10), total - 1) : total - 1;
    } else {
      start = Math.max(0, total - parseInt(m[2] || '0', 10));
      end = total - 1;
    }
    if (start >= total || start > end) {
      return new Response(null, { status: 416, statusText: 'Range Not Satisfiable', headers: { 'Content-Range': 'bytes */' + total } });
    }
    var part = blob.slice(start, end + 1, blob.type);
    var headers = new Headers();
    headers.set('Content-Type', res.headers.get('Content-Type') || blob.type || 'application/octet-stream');
    headers.set('Content-Range', 'bytes ' + start + '-' + end + '/' + total);
    headers.set('Content-Length', String(part.size));
    headers.set('Accept-Ranges', 'bytes');
    return new Response(part, { status: 206, statusText: 'Partial Content', headers: headers });
  });
}

function offlinePage() {
  var html = '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">' +
    '<title>Offline — not saved yet</title><style>body{background:#0a0a0a;color:#f2f2f2;font:600 18px/1.6 "Share Tech Mono",Consolas,monospace;margin:0;padding:48px 24px}' +
    'a{color:#f2f2f2}p{max-width:560px}.dim{color:#9a9a9a}</style></head><body><p>[ offline ]</p>' +
    '<p>This page has not been saved on this device yet.</p><p class="dim">Connect to the internet once, open <a href="offline.html">offline.html</a> and tap "save for offline" to store the whole site.</p>' +
    '<p><a href="index.html">← home</a></p></body></html>';
  return new Response(html, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}
