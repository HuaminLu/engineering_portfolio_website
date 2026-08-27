// Drag-to-scroll for the home tile row. Click-drag anywhere on the row to
// pan it; a real drag suppresses the tile's click so links don't fire.
(function () {
  var row = document.querySelector(".tiles");
  if (!row) return;

  var isDown = false;
  var moved = false;
  var startX = 0;
  var startScroll = 0;

  row.addEventListener("pointerdown", function (e) {
    if (e.pointerType !== "mouse" || e.button !== 0) return; // touch scrolls natively
    isDown = true;
    moved = false;
    startX = e.clientX;
    startScroll = row.scrollLeft;
    row.classList.add("dragging");
  });

  window.addEventListener("pointermove", function (e) {
    if (!isDown) return;
    var dx = e.clientX - startX;
    if (Math.abs(dx) > 5) moved = true;
    row.scrollLeft = startScroll - dx;
  });

  window.addEventListener("pointerup", function () {
    isDown = false;
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
