// js/offline.js — phone / offline support. Loaded ONLY by offline.html.
// 1. registers the service worker (sw.js) for this browser (opt-in)
// 2. "save for offline": crawls every same-site page reachable from index.html,
//    collects their images, videos, posters, scripts and styles, and stores
//    everything in the service-worker cache with a progress bar
// 3. "install": surfaces Chrome's Add-to-Home-screen prompt when available
// 4. "remove": deletes the cache and unregisters the worker
(function () {
  'use strict';
  var CACHE = 'portfolio-v1';
  var page = document.querySelector('[data-offline-page]');
  if (!page) return;
  var $ = function (sel) { return page.querySelector(sel); };
  var statusEl = $('[data-status]'), barEl = $('[data-bar]'), usageEl = $('[data-usage]'), logEl = $('[data-log]');
  var saveBtn = $('[data-save]'), clearBtn = $('[data-clear]'), installBtn = $('[data-install]'), installHint = $('[data-install-hint]');

  function setStatus(t) { statusEl.textContent = t; }
  function setBar(f) { barEl.style.width = Math.max(0, Math.min(100, Math.round(f * 100))) + '%'; }
  function fmtMB(b) { return (b / 1048576).toFixed(1) + ' MB'; }
  function log(t) { var li = document.createElement('li'); li.textContent = t; logEl.appendChild(li); }

  var supported = 'serviceWorker' in navigator && location.protocol !== 'file:';
  if (!supported) {
    setStatus(location.protocol === 'file:'
      ? 'this is already the offline copy on disk — nothing to save here.'
      : 'this browser cannot store sites for offline use.');
    saveBtn.disabled = true; clearBtn.disabled = true;
    return;
  }

  // --- 1. register ---------------------------------------------------------
  var registration = navigator.serviceWorker.register('sw.js').then(function (reg) {
    return navigator.serviceWorker.ready.then(function () { return reg; });
  }).catch(function (err) { setStatus('could not start offline support: ' + err.message); return null; });

  // --- 3. install prompt ---------------------------------------------------
  var deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    installBtn.hidden = false;
    installHint.hidden = true;
  });
  installBtn.addEventListener('click', function () {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then(function () { deferredPrompt = null; installBtn.hidden = true; });
  });
  window.addEventListener('appinstalled', function () { log('installed on the home screen'); });
  if (window.matchMedia('(display-mode: standalone)').matches) {
    installHint.textContent = 'running from the home screen icon.';
  }

  // --- status / usage --------------------------------------------------------
  function refreshUsage() {
    return caches.has(CACHE).then(function (has) {
      return has ? caches.open(CACHE).then(function (cache) { return cache.keys(); }) : [];
    }).then(function (keys) {
      var n = keys.length;
      var est = (navigator.storage && navigator.storage.estimate) ? navigator.storage.estimate() : Promise.resolve(null);
      return est.then(function (e) {
        usageEl.textContent = (n ? n + ' files saved on this device' : 'nothing saved on this device yet') +
          (e && e.usage ? ' · ' + fmtMB(e.usage) + ' used' : '') +
          (e && e.quota ? ' · ' + fmtMB(e.quota) + ' available' : '');
        return n;
      });
    }).catch(function () { return 0; });
  }
  registration.then(function () { refreshUsage().then(function (n) { if (n) setStatus('ready — saved copy is on this device. tap save again to refresh it.'); else setStatus('ready.'); }); });

  // --- 2. crawl + save -----------------------------------------------------
  var ASSET_SEL = 'img[src], video[poster], video[src], source[src], link[rel="stylesheet"][href], script[src], link[rel="icon"][href], link[rel="manifest"][href], a[href$=".pdf"]';

  function collect() {
    var pages = [], seen = {}, assets = {}, queue = [new URL('index.html', location.href).href];
    function next() {
      if (!queue.length) return Promise.resolve();
      var u = queue.shift();
      if (seen[u]) return next();
      seen[u] = true;
      return fetch(u, { cache: 'no-cache' }).then(function (r) {
        if (!r.ok) throw new Error(r.status + ' ' + u);
        return r.text();
      }).then(function (html) {
        pages.push(u);
        var doc = new DOMParser().parseFromString(html, 'text/html');
        doc.querySelectorAll('a[href], iframe[src]').forEach(function (a) {
          var h = a.getAttribute('href') || a.getAttribute('src');
          if (!h || /^(https?:|mailto:|#|javascript:)/i.test(h)) return;
          var abs = new URL(h, u); abs.hash = '';
          if (abs.origin === location.origin && /\.html$/.test(abs.pathname) && !seen[abs.href]) queue.push(abs.href);
        });
        doc.querySelectorAll(ASSET_SEL).forEach(function (el) {
          var v = el.getAttribute('src') || el.getAttribute('poster') || el.getAttribute('href');
          if (!v || /^(data:|javascript:)/i.test(v)) return;
          var abs = new URL(v, u); abs.hash = '';
          assets[abs.href] = true;
        });
        setStatus('finding pages and files… ' + pages.length + ' pages, ' + Object.keys(assets).length + ' files');
      }).catch(function (err) { log('skipped page: ' + err.message); }).then(next);
    }
    return next().then(function () { return { pages: pages, assets: Object.keys(assets) }; });
  }

  function saveAll() {
    saveBtn.disabled = true; clearBtn.disabled = true;
    logEl.textContent = '';
    setBar(0); setStatus('starting…');
    var persist = (navigator.storage && navigator.storage.persist) ? navigator.storage.persist().catch(function () {}) : Promise.resolve();
    return persist.then(function () { return registration; }).then(function (reg) {
      if (!reg) throw new Error('offline support unavailable');
      return collect();
    }).then(function (found) {
      // Cross-origin stylesheets (Google Fonts, Font Awesome) reference font
      // files we cannot see from the HTML. Read each one and add what it uses.
      var sheets = found.assets.filter(function (u) { return new URL(u).origin !== location.origin && /\.css(\?|$)|fonts\.googleapis\.com/.test(u); });
      return Promise.all(sheets.map(function (u) {
        return fetch(u).then(function (r) { return r.ok ? r.text() : ''; }).then(function (css) {
          var re = /url\(\s*['"]?([^'")]+)['"]?\s*\)/g, m, extra = [];
          while ((m = re.exec(css))) { if (!/^data:/.test(m[1])) extra.push(new URL(m[1], u).href); }
          return extra;
        }).catch(function () { return []; });
      })).then(function (lists) {
        var extra = [].concat.apply([], lists);
        return { pages: found.pages, assets: found.assets.concat(extra) };
      });
    }).then(function (found) {
      var urls = found.pages.concat(found.assets, [new URL('offline.html', location.href).href, new URL('js/offline.js', location.href).href]);
      var uniq = {}; urls = urls.filter(function (u) { if (uniq[u]) return false; uniq[u] = true; return true; });
      var total = urls.length, done = 0, bytes = 0, failed = 0;
      return caches.open(CACHE).then(function (cache) {
        function worker() {
          if (!urls.length) return Promise.resolve();
          var u = urls.shift();
          var same = new URL(u).origin === location.origin;
          var isFont = /\.(woff2?|ttf|otf)(\?|$)/.test(u);
          // fonts must be stored as CORS responses (that is how @font-face asks for them);
          // other cross-origin files (CDN scripts, stylesheets) are stored opaque.
          var req = new Request(u, same ? { cache: 'no-cache' } : (isFont ? { mode: 'cors' } : { mode: 'no-cors' }));
          return fetch(req).then(function (res) {
            if ((same || isFont) && !res.ok) throw new Error(res.status);
            bytes += (+res.headers.get('content-length')) || 0;
            return cache.put(new Request(u), res);
          }).catch(function (err) {
            failed++; log('failed: ' + u.replace(location.origin, '') + ' (' + (err.message || err) + ')');
          }).then(function () {
            done++; setBar(done / total);
            setStatus('saving ' + done + ' / ' + total + ' files · ' + fmtMB(bytes));
            return worker();
          });
        }
        return Promise.all([worker(), worker(), worker(), worker()]).then(function () {
          setBar(1);
          setStatus('done — ' + (total - failed) + ' files (' + fmtMB(bytes) + ') saved. the site now works without internet.' + (failed ? ' ' + failed + ' files failed, see below.' : ''));
        });
      });
    }).catch(function (err) {
      setStatus('could not save: ' + (err.message || err));
    }).then(function () {
      saveBtn.disabled = false; clearBtn.disabled = false;
      return refreshUsage();
    });
  }

  // --- 4. remove -----------------------------------------------------------
  function clearAll() {
    saveBtn.disabled = true; clearBtn.disabled = true;
    return caches.delete(CACHE).then(function () {
      return navigator.serviceWorker.getRegistrations();
    }).then(function (regs) {
      return Promise.all(regs.map(function (r) { return r.unregister(); }));
    }).then(function () {
      setBar(0); setStatus('offline data removed. reload this page to set it up again.');
      logEl.textContent = '';
    }).catch(function (err) { setStatus('could not remove: ' + (err.message || err)); }).then(function () {
      saveBtn.disabled = false; clearBtn.disabled = false;
      return refreshUsage();
    });
  }

  saveBtn.addEventListener('click', saveAll);
  clearBtn.addEventListener('click', clearAll);
  window.__offline = { saveAll: saveAll, clearAll: clearAll, collect: collect }; // for tests
})();
