// Scroll-reveal: fades content up as it enters the viewport. One-time, subtle.
(function () {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var targets = document.querySelectorAll(
    ".tile, .grid-tile, .subproject, .fig-wide, .fig-split, .fig-row, " +
    ".achievements li, .spec-list, .calc, pre, .project-intro, " +
    ".about p, .about-links"
  );

  targets.forEach(function (el) { el.classList.add("reveal"); });

  // about portrait slides in from the left instead of fading up
  var photo = document.querySelector(".about-photo");
  if (photo) photo.classList.add("reveal", "reveal-left");

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
  if (photo) observer.observe(photo);
})();
