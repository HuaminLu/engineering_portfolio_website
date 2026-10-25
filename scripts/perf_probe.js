// scripts/perf_probe.js — browser-side diagnostics for the portfolio.
// Paste this file into the DevTools console (or inject it with a <script> tag),
// then call e.g.  await __probe.measure(5000)  /  __probe.snapshot()
// It is not loaded by any page; it exists so the freeze fix can be re-verified.
(function () {
  function rafMeasure(ms) {
    return new Promise(function (resolve) {
      var frames = 0, long50 = 0, long100 = 0, long250 = 0, maxGap = 0;
      var t0 = performance.now(), last = t0;
      function f(t) {
        var d = t - last; last = t; frames++;
        if (d > 50) long50++; if (d > 100) long100++; if (d > 250) long250++;
        if (d > maxGap) maxGap = d;
        if (t - t0 < ms) requestAnimationFrame(f);
        else resolve({ secs: Math.round((last - t0) / 100) / 10, frames: frames,
          fps: Math.round(frames / ((last - t0) / 1000)), long50: long50, long100: long100,
          long250: long250, maxGapMs: Math.round(maxGap) });
      }
      requestAnimationFrame(f);
    });
  }

  function snapshot() {
    var vids = Array.prototype.slice.call(document.querySelectorAll("video"));
    var imgs = Array.prototype.slice.call(document.querySelectorAll("img"));
    var rs = performance.getEntriesByType("resource");
    var m = window.__marquee;
    return {
      url: location.pathname, viewport: innerWidth + "x" + innerHeight, visibility: document.visibilityState,
      videos: vids.length, playing: vids.filter(function (v) { return !v.paused; }).length,
      rates: vids.filter(function (v) { return !v.paused; }).map(function (v) { return v.playbackRate; }),
      imgs: imgs.length, webpImgs: imgs.filter(function (i) { return /\.webp/.test(i.currentSrc || i.src); }).length,
      marquee: m ? { tiles: m.tiles(), awake: m.awakeCount(), playing: m.playingCount(), pos: Math.round(m.pos()), wrap: Math.round(m.wrapDist()) } : null,
      offscreenTiles: document.querySelectorAll(".tile.is-offscreen").length,
      heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
      requests: rs.length,
      transferMB: Math.round(rs.reduce(function (s, r) { return s + (r.transferSize || 0); }, 0) / 1048576 * 10) / 10
    };
  }

  // Synthetic pointer drag on the home marquee (dx < 0 scrolls forward).
  function drag(dx, steps) {
    var row = document.querySelector(".tiles");
    if (!row) return false;
    steps = steps || 8;
    var r = row.getBoundingClientRect();
    var x0 = r.left + r.width / 2, y = r.top + r.height / 2;
    function ev(type, target, x) {
      target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerType: "mouse", button: 0, isPrimary: true }));
    }
    ev("pointerdown", row, x0);
    for (var i = 1; i <= steps; i++) ev("pointermove", window, x0 + dx * i / steps);
    ev("pointerup", window, x0 + dx);
    return true;
  }

  function hover(on) {
    var row = document.querySelector(".tiles");
    if (!row) return false;
    row.dispatchEvent(new MouseEvent(on ? "mouseenter" : "mouseleave", { bubbles: false }));
    return true;
  }

  // Pretend the tab was hidden / shown again.
  var hiddenOverride = null;
  function setHidden(flag) {
    if (hiddenOverride === null) {
      hiddenOverride = { hidden: Object.getOwnPropertyDescriptor(Document.prototype, "hidden"),
        vs: Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState") };
    }
    if (flag === null) {
      delete document.hidden; delete document.visibilityState;
    } else {
      Object.defineProperty(document, "hidden", { configurable: true, get: function () { return flag; } });
      Object.defineProperty(document, "visibilityState", { configurable: true, get: function () { return flag ? "hidden" : "visible"; } });
    }
    document.dispatchEvent(new Event("visibilitychange"));
  }

  window.__probe = { measure: rafMeasure, snapshot: snapshot, drag: drag, hover: hover, setHidden: setHidden };
})();
