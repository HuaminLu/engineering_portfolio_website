// Home tile row: infinite marquee that auto-scrolls slowly, pauses on hover,
// and supports click-drag panning. Tiles are duplicated once so the loop
// wraps seamlessly at the halfway point.
//
// Media virtualization (desktop freeze fix): only the tiles that are on screen,
// plus one tile-width of margin on each side, keep their <video> playing and
// their image area painted. Every other tile is "parked": its video is paused
// and its .tile-img gets visibility:hidden (see .tile.is-offscreen in
// css/style.css), so animated WebPs and photos in it are neither painted nor
// decoded. Without this, the cloned track meant 12 hardware video decoders and
// 22 animated WebPs ran at once inside one moving compositor layer, which hangs
// Edge/Chrome/Firefox on ordinary laptops.
//
// A tile and its clone are "twins". Just before the loop wraps, the twin that is
// about to replace the visible one is woken early and synced (video time and
// gallery frame), so the wrap is still seamless.
(function () {
  var row = document.querySelector(".tiles");
  if (!row) return;

  // --- exact fold sizing: measure instead of hardcoding header math ---
  var fold = document.querySelector(".fold");
  function sizeFold() {
    if (!fold) return;
    if (window.innerWidth <= 640) {
      fold.style.height = "auto";
      return;
    }
    // distance from document top to the fold, immune to scroll position
    var topOffset = fold.getBoundingClientRect().top + window.scrollY;
    var available = window.innerHeight - topOffset;
    fold.style.height = available > 360 ? available + "px" : "";
  }
  sizeFold();
  window.addEventListener("resize", sizeFold);
  window.addEventListener("load", sizeFold); // re-measure once fonts settle

  // --- build hardware-accelerated transform track & duplicate tiles once ---
  var originals = Array.prototype.slice.call(row.children);
  var track = document.createElement("div");
  track.className = "tiles-track";

  originals.forEach(function (tile) {
    track.appendChild(tile);
  });

  originals.forEach(function (tile) {
    var clone = tile.cloneNode(true);
    clone.querySelectorAll(".fade-gallery").forEach(function (g) {
      delete g.dataset.fadeInit;
    });
    clone.querySelectorAll("video").forEach(function (v) {
      delete v.dataset.speedInit;
      // A clone must not open its own decoder the moment it is inserted:
      // no autoplay and no preload. It is started on demand by wakeTile().
      v.removeAttribute("autoplay");
      v.autoplay = false;
      v.preload = "none";
    });
    clone.classList.remove("reveal", "visible");
    clone.setAttribute("aria-hidden", "true");
    clone.setAttribute("tabindex", "-1");
    track.appendChild(clone);
  });

  row.appendChild(track);

  if (window.initFadeGalleries) {
    window.initFadeGalleries();
  }
  if (window.scanVideos) {
    window.scanVideos();
  }

  // --- equalize tile text sections so all image areas are the same height ---
  function equalizeBodies() {
    var bodies = track.querySelectorAll(".tile-body");
    var max = 0;
    bodies.forEach(function (b) { b.style.height = "auto"; });
    bodies.forEach(function (b) { max = Math.max(max, b.offsetHeight); });
    bodies.forEach(function (b) { b.style.height = max + "px"; });
  }
  equalizeBodies();
  window.addEventListener("resize", equalizeBodies);
  window.addEventListener("load", equalizeBodies);

  // --- media virtualization state ---
  var tileInfo = Array.prototype.slice.call(track.children).map(function (tile) {
    return {
      el: tile,
      left: 0,
      width: 0,
      videos: Array.prototype.slice.call(tile.querySelectorAll("video")),
      galleries: Array.prototype.slice.call(tile.querySelectorAll(".fade-gallery")),
      awake: true, // matches the DOM as parsed: everything starts playing
      twin: null
    };
  });
  var n = originals.length;
  tileInfo.forEach(function (t, i) { t.twin = tileInfo[(i + n) % tileInfo.length]; });

  var viewW = row.clientWidth;
  var maxTileW = 0;
  function measureTiles() {
    viewW = row.clientWidth;
    maxTileW = 0;
    tileInfo.forEach(function (t) {
      t.left = t.el.offsetLeft;
      t.width = t.el.offsetWidth;
      if (t.width > maxTileW) maxTileW = t.width;
    });
  }

  var rowVisible = true;              // vertical IntersectionObserver on the row
  var pageVisible = !document.hidden; // tab visibility
  var pos = 0;
  var wrapDist = 0;
  var pressed = false;                // pointer currently down on the row (drag)

  // Put video `v` at the same time as its twin `sv`. If the twin has not
  // loaded metadata yet (it was itself woken a moment ago by a fast drag),
  // do it as soon as it has, so the pair never drifts apart at the seam.
  function syncVideo(v, sv, t) {
    var tries = 0;
    function apply() {
      try {
        if (Math.abs(v.currentTime - sv.currentTime) > 0.08) {
          v.currentTime = sv.currentTime;
          // A seek lands slightly late (keyframe decode); correct once more.
          if (tries++ < 2) v.addEventListener("seeked", apply, { once: true });
        }
      } catch (e) {}
    }
    if (sv.readyState >= 1 && !sv.paused) { apply(); return; }
    // Twin is still loading / about to start: sync the moment it really plays.
    var onPlaying = function () {
      sv.removeEventListener("playing", onPlaying);
      if (t.awake) apply();
    };
    sv.addEventListener("playing", onPlaying);
  }

  function syncFromTwin(t) {
    var s = t.twin;
    if (!s || !s.awake) return;
    t.videos.forEach(function (v, i) {
      var sv = s.videos[i];
      if (sv) syncVideo(v, sv, t);
    });
    t.galleries.forEach(function (g, i) {
      var sg = s.galleries[i];
      if (g._fade && sg && sg._fade) g._fade.syncFrom(sg._fade);
    });
  }

  function wakeTile(t, syncTwin) {
    if (t.awake) return;
    t.awake = true;
    t.el.classList.remove("is-offscreen");
    if (syncTwin) syncFromTwin(t);
    t.galleries.forEach(function (g) { if (g._fade) g._fade.unpark(); });
    t.videos.forEach(function (v) {
      if (v.preload === "none") v.preload = "auto";
      var p = v.play();
      if (p !== undefined) p.catch(function () {});
    });
  }

  function parkTile(t) {
    if (!t.awake) return;
    t.awake = false;
    t.el.classList.add("is-offscreen");
    t.videos.forEach(function (v) { v.pause(); });
    t.galleries.forEach(function (g) { if (g._fade) g._fade.park(); });
  }

  // Decide which tiles are on screen for the current scroll position. Cheap
  // (a few comparisons per tile, no layout reads), so it runs every frame the
  // track moves; DOM writes only happen when a tile changes state.
  var lastCullPos = NaN;
  function cull(force) {
    if (!force && Math.abs(pos - lastCullPos) < 16) return;
    lastCullPos = pos;
    var margin = Math.max(maxTileW, 240); // one (widest) tile of lead on each side
    var winL = pos - margin;
    var winR = pos + viewW + margin;
    var allOn = rowVisible && pageVisible;
    // Near either seam, also wake the twin that takes over after the wrap.
    var preWakeOriginals = wrapDist > 0 && (wrapDist - pos) < margin; // scrolling right, about to wrap to 0
    var preWakeClones = wrapDist > 0 && pressed && pos < margin;        // being dragged left, about to wrap to end

    // Two passes: normal wakes/parks first, twin-driven wakes second, so a
    // pre-woken tile always finds its twin already awake to sync from.
    var viaTwinList = [];
    tileInfo.forEach(function (t, i) {
      var l = t.left, r = t.left + t.width;
      var on = allOn && r > winL && l < winR;
      if (on) { wakeTile(t, false); return; }
      var viaTwin = false;
      if (allOn && wrapDist > 0) {
        if (i < n && preWakeOriginals) {
          viaTwin = (r + wrapDist) > winL && (l + wrapDist) < winR;
        } else if (i >= n && preWakeClones) {
          viaTwin = (r - wrapDist) > winL && (l - wrapDist) < winR;
        }
      }
      if (viaTwin) viaTwinList.push(t); else parkTile(t);
    });
    viaTwinList.forEach(function (t) { wakeTile(t, true); });
  }

  // --- measure exact wrap distance (distance between original 0 and clone 0) ---
  function updateWrapDist() {
    var all = track.children;
    if (all.length > originals.length) {
      wrapDist = all[originals.length].offsetLeft - all[0].offsetLeft;
    }
    measureTiles();
    cull(true);
  }
  updateWrapDist();
  window.addEventListener("resize", updateWrapDist);
  window.addEventListener("load", updateWrapDist);

  // --- strictly constant-speed auto-scroll using delta-time & GPU transform ---
  var SPEED = 75; // pixels per second (strictly constant across all refresh rates & devices)
  var paused = false;
  var lastTime = null;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var resumeTimer = null;

  function pauseNow() {
    clearTimeout(resumeTimer);
    paused = true;
  }

  function resumeAfter(ms) {
    clearTimeout(resumeTimer);
    resumeTimer = setTimeout(function () {
      paused = false;
      lastTime = performance.now(); // reset delta clock so there is no jump
    }, ms);
  }

  row.addEventListener("mouseenter", pauseNow);
  row.addEventListener("mouseleave", function () {
    paused = false;
    lastTime = performance.now();
  });
  row.addEventListener("touchstart", pauseNow, { passive: true });
  row.addEventListener("touchend", function () { resumeAfter(1200); });

  // Park everything (rAF keeps ticking cheaply) when the row is scrolled off
  // screen vertically or the tab is hidden; wake what is on screen on return.
  if ("IntersectionObserver" in window) {
    var rowObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        rowVisible = entry.isIntersecting;
        if (rowVisible) lastTime = performance.now();
        cull(true);
      });
    }, { rootMargin: "150px 0px" });
    rowObserver.observe(row);
  }

  document.addEventListener("visibilitychange", function () {
    pageVisible = !document.hidden;
    if (pageVisible) lastTime = performance.now();
    cull(true);
  });

  function tick(now) {
    if (!lastTime) lastTime = now;
    var dt = Math.min((now - lastTime) / 1000, 0.1);
    lastTime = now;

    if (!reduced && !paused && !pressed && rowVisible && pageVisible) {
      pos += SPEED * dt;
      if (wrapDist > 0 && pos >= wrapDist) {
        pos -= wrapDist;
      }
      track.style.transform = "translate3d(-" + pos.toFixed(2) + "px, 0, 0)";
      cull(false);
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // --- drag to scroll ---
  var moved = false;
  var isHorizontalDrag = false;
  var startX = 0;
  var startY = 0;
  var startPos = 0;

  // Kill the browser's native link/text drag that would hijack the gesture.
  row.addEventListener("dragstart", function (e) { e.preventDefault(); });

  row.addEventListener("pointerdown", function (e) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    pressed = true;
    moved = false;
    isHorizontalDrag = false;
    startX = e.clientX;
    startY = e.clientY;
    startPos = pos;
  });

  window.addEventListener("pointermove", function (e) {
    if (!pressed) return;
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;

    if (!moved) {
      if (Math.abs(dx) > 6 && Math.abs(dx) > Math.abs(dy)) {
        moved = true;
        isHorizontalDrag = true;
        row.classList.add("dragging");
      } else if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {
        pressed = false;
        return;
      }
    }
    if (!moved || !isHorizontalDrag) return;
    pos = startPos - dx;
    if (wrapDist > 0) {
      while (pos >= wrapDist) { pos -= wrapDist; startPos -= wrapDist; }
      while (pos < 0) { pos += wrapDist; startPos += wrapDist; }
    }
    track.style.transform = "translate3d(-" + pos.toFixed(2) + "px, 0, 0)";
    cull(false);
  });

  window.addEventListener("pointerup", function () {
    if (!pressed) return;
    pressed = false;
    row.classList.remove("dragging");
    lastTime = performance.now();
    resumeAfter(300);
  });

  window.addEventListener("pointercancel", function () {
    if (!pressed) return;
    pressed = false;
    row.classList.remove("dragging");
    lastTime = performance.now();
    resumeAfter(300);
  });

  // Swallow only the click that ends a real drag; plain clicks navigate.
  row.addEventListener(
    "click",
    function (e) {
      if (moved) {
        e.preventDefault();
        e.stopPropagation();
        moved = false;
      }
    },
    true
  );

  // Read-only hook for diagnostics/tests (not used by the page itself).
  window.__marquee = {
    awakeCount: function () { return tileInfo.filter(function (t) { return t.awake; }).length; },
    playingCount: function () { return tileInfo.reduce(function (c, t) { return c + t.videos.filter(function (v) { return !v.paused; }).length; }, 0); },
    pos: function () { return pos; },
    wrapDist: function () { return wrapDist; },
    tiles: function () { return tileInfo.length; },
    viewW: function () { return viewW; },
    state: function () { return tileInfo.map(function (t) { return { l: t.left, w: t.width, awake: t.awake, playing: t.videos.filter(function (v) { return !v.paused; }).length }; }); }
  };
})();
