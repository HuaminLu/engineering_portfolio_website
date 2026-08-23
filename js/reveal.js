// Scroll-reveal: fades content up as it enters the viewport. One-time, subtle.
(function () {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var targets = document.querySelectorAll(
    ".tile, .grid-tile, .subproject, .fig-wide, .fig-split, .fig-row, " +
    ".achievements li, .spec-list, .calc, pre, .about, .project-intro"
  );

  targets.forEach(function (el) { el.classList.add("reveal"); });

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
  );

  targets.forEach(function (el) { observer.observe(el); });
})();
