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

  function halfWidth() {
    return row.scrollWidth / 2;
  }

  function wrap() {
    var half = halfWidth();
    if (half <= 0) return;
    if (row.scrollLeft >= half) row.scrollLeft -= half;
    else if (row.scrollLeft < 0) row.scrollLeft += half;
  }

  // --- auto-scroll (pauses on hover / drag / touch) ---
  var paused = false;
  var dragging = false;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SPEED = 0.6; // px per frame ≈ 36 px/s

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
  row.addEventListener("mouseleave", function () { resumeAfter(1000); });
  row.addEventListener("touchstart", pauseNow, { passive: true });
  row.addEventListener("touchend", function () { resumeAfter(1500); });

  function tick() {
    if (!reduced && !paused && !dragging) {
      row.scrollLeft += SPEED;
    }
    wrap();
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // --- drag to scroll ---
  var moved = false;
  var startX = 0;
  var startScroll = 0;

  // Kill the browser's native link/text drag that was hijacking the gesture.
  row.addEventListener("dragstart", function (e) { e.preventDefault(); });

  row.addEventListener("pointerdown", function (e) {
    if (e.pointerType !== "mouse" || e.button !== 0) return; // touch pans natively
    dragging = true;
    moved = false;
    startX = e.clientX;
    startScroll = row.scrollLeft;
    row.classList.add("dragging");
    e.preventDefault();
  });

  window.addEventListener("pointermove", function (e) {
    if (!dragging) return;
    var dx = e.clientX - startX;
    if (Math.abs(dx) > 5) moved = true;
    row.scrollLeft = startScroll - dx;
    wrap();
    // Re-anchor after a wrap so continued dragging stays smooth.
    startX = e.clientX;
    startScroll = row.scrollLeft;
  });

  window.addEventListener("pointerup", function () {
    dragging = false;
    row.classList.remove("dragging");
  });

  // Swallow the click that ends a drag so tiles don't navigate.
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
