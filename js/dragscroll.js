// Home tile row: infinite marquee that auto-scrolls slowly, pauses on hover,
// and supports click-drag panning. Tiles are duplicated once so the loop
// wraps seamlessly at the halfway point.
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
    clone.querySelectorAll('.fade-gallery').forEach(function(g) {
      delete g.dataset.fadeInit;
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

  // --- measure exact wrap distance (distance between original 0 and clone 0) ---
  var wrapDist = 0;
  function updateWrapDist() {
    var all = track.children;
    if (all.length > originals.length) {
      wrapDist = all[originals.length].offsetLeft - all[0].offsetLeft;
    }
  }
  updateWrapDist();
  window.addEventListener("resize", updateWrapDist);
  window.addEventListener("load", updateWrapDist);

  // --- strictly constant-speed auto-scroll using delta-time & GPU transform ---
  var SPEED = 75; // pixels per second (strictly constant across all refresh rates & devices)
  var pos = 0;
  var paused = false;
  var pressed = false;
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

  function tick(now) {
    if (!lastTime) lastTime = now;
    var dt = Math.min((now - lastTime) / 1000, 0.1);
    lastTime = now;

    if (!reduced && !paused && !pressed) {
      pos += SPEED * dt;
      if (wrapDist > 0 && pos >= wrapDist) {
        pos -= wrapDist;
      }
      track.style.transform = "translate3d(-" + pos.toFixed(2) + "px, 0, 0)";
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
})();
