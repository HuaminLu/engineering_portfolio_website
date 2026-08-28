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
    // distance from document top to the fold, immune to scroll position
    var topOffset = fold.getBoundingClientRect().top + window.scrollY;
    var available = window.innerHeight - topOffset;
    fold.style.height = available > 430 ? available + "px" : "";
  }
  sizeFold();
  window.addEventListener("resize", sizeFold);
  window.addEventListener("load", sizeFold); // re-measure once fonts settle

  // --- seamless loop: duplicate the tile set once ---
  var originals = Array.prototype.slice.call(row.children);
  originals.forEach(function (tile) {
    var clone = tile.cloneNode(true);
    clone.classList.remove("reveal", "visible");
    clone.setAttribute("aria-hidden", "true");
    clone.setAttribute("tabindex", "-1");
    row.appendChild(clone);
  });

  // --- equalize tile text sections so all image areas are the same height ---
  function equalizeBodies() {
    var bodies = row.querySelectorAll(".tile-body");
    var max = 0;
    bodies.forEach(function (b) { b.style.height = "auto"; });
    bodies.forEach(function (b) { max = Math.max(max, b.offsetHeight); });
    bodies.forEach(function (b) { b.style.height = max + "px"; });
  }
  equalizeBodies();
  window.addEventListener("resize", equalizeBodies);
  window.addEventListener("load", equalizeBodies);

  function halfWidth() {
    return row.scrollWidth / 2;
  }

  // --- auto-scroll ---
  // pos is a float accumulator: scrollLeft itself rounds to whole pixels,
  // so adding 0.6px/frame directly to it would round to zero movement.
  var pos = 0;
  var paused = false;
  var pressed = false;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SPEED = 1.8; // px per frame ≈ 108 px/s

  var resumeTimer = null;

  function pauseNow() {
    clearTimeout(resumeTimer);
    paused = true;
  }

  function resumeAfter(ms) {
    clearTimeout(resumeTimer);
    resumeTimer = setTimeout(function () { paused = false; }, ms);
  }

  row.addEventListener("mouseenter", pauseNow);
  row.addEventListener("mouseleave", function () { paused = false; }); // resume instantly
  row.addEventListener("touchstart", pauseNow, { passive: true });
  row.addEventListener("touchend", function () { resumeAfter(1500); });

  function tick() {
    if (!reduced && !paused && !pressed) {
      pos += SPEED;
      var half = halfWidth();
      if (half > 0 && pos >= half) pos -= half;
      row.scrollLeft = pos;
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // --- drag to scroll ---
  var moved = false;
  var startX = 0;
  var startScroll = 0;

  // Kill the browser's native link/text drag that would hijack the gesture.
  row.addEventListener("dragstart", function (e) { e.preventDefault(); });

  row.addEventListener("pointerdown", function (e) {
    if (e.pointerType !== "mouse" || e.button !== 0) return; // touch pans natively
    pressed = true;
    moved = false;
    startX = e.clientX;
    startScroll = row.scrollLeft;
  });

  window.addEventListener("pointermove", function (e) {
    if (!pressed) return;
    var dx = e.clientX - startX;
    // Only becomes a drag after real movement — a plain click stays a click.
    if (!moved && Math.abs(dx) > 6) {
      moved = true;
      row.classList.add("dragging");
    }
    if (!moved) return;
    var next = startScroll - dx;
    var half = halfWidth();
    if (half > 0) {
      if (next >= half) { next -= half; startScroll -= half; }
      else if (next < 0) { next += half; startScroll += half; }
    }
    row.scrollLeft = next;
  });

  window.addEventListener("pointerup", function () {
    if (!pressed) return;
    pressed = false;
    row.classList.remove("dragging");
    pos = row.scrollLeft; // resync the accumulator with where the user left it
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
