// Scroll-reveal: fades content up as it enters the viewport. One-time, subtle.
(function () {
  // Project-page fold: size title + stats + intro + hero to exactly one screen.
  // Runs regardless of reduced-motion (it's layout, not animation).
  var pfold = document.querySelector(".pfold");
  function sizePfold() {
    if (!pfold) return;
    var top = pfold.getBoundingClientRect().top + window.scrollY;
    var available = window.innerHeight - top;
    pfold.style.height = available > 460 ? available + "px" : "";
  }
  sizePfold();
  window.addEventListener("resize", sizePfold);
  window.addEventListener("load", sizePfold);

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
