// Scroll-reveal, Navigation & Micro-Interactions
(function () {
  // 1. Project-page fold: size title + stats + intro + hero without clamping
  var pfold = document.querySelector(".pfold");
  function sizePfold() {
    if (!pfold) return;
    pfold.style.height = "auto";
    var top = pfold.getBoundingClientRect().top + window.scrollY;
    var available = window.innerHeight - top;
    if (available > 460 && pfold.offsetHeight < available) {
      pfold.style.minHeight = available + "px";
    } else {
      pfold.style.minHeight = "";
    }
  }
  sizePfold();
  window.addEventListener("resize", sizePfold);
  window.addEventListener("load", sizePfold);

  // 2. Dropdown tap/click support for mobile & desktop accessibility
  var dropdown = document.querySelector(".dropdown");
  if (dropdown) {
    var trigger = dropdown.querySelector("a");
    if (trigger) {
      trigger.addEventListener("click", function (e) {
        // Toggle menu on touch / click
        if (window.innerWidth <= 860 || trigger.getAttribute("href") === "#") {
          e.preventDefault();
          dropdown.classList.toggle("open");
        }
      });
    }
    // Close dropdown when clicking outside
    document.addEventListener("click", function (e) {
      if (!dropdown.contains(e.target)) {
        dropdown.classList.remove("open");
      }
    });
  }

  // 3. Floating "Back to Top" Monospace Button
  var backToTopBtn = document.createElement("button");
  backToTopBtn.className = "back-to-top";
  backToTopBtn.setAttribute("aria-label", "Scroll back to top");
  backToTopBtn.innerHTML = "[ ↑ top ]";
  document.body.appendChild(backToTopBtn);

  function checkScrollTop() {
    if (window.scrollY > 420) {
      backToTopBtn.classList.add("visible");
    } else {
      backToTopBtn.classList.remove("visible");
    }
  }
  window.addEventListener("scroll", checkScrollTop, { passive: true });
  backToTopBtn.addEventListener("click", function () {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  // 4. Click-to-copy email with toast notification
  var copyToast = document.createElement("div");
  copyToast.className = "copy-toast";
  copyToast.textContent = "[ email copied to clipboard! ]";
  document.body.appendChild(copyToast);

  var toastTimer = null;
  document.querySelectorAll('a[href^="mailto:"]').forEach(function (link) {
    link.addEventListener("click", function (e) {
      var email = link.getAttribute("href").replace(/^mailto:/, "");
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(email).then(function () {
          copyToast.classList.add("show");
          clearTimeout(toastTimer);
          toastTimer = setTimeout(function () {
            copyToast.classList.remove("show");
          }, 2200);
        }).catch(function () {});
      }
    });
  });

  // 5. Scroll entrance reveal animations
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var targets = document.querySelectorAll(
    ".grid-tile, .subproject, .fig-wide, .fig-split, .fig-row, " +
    ".achievements li, .spec-list, .calc, pre, .project-intro, " +
    ".about p, .about-links"
  );

  targets.forEach(function (el) { el.classList.add("reveal"); });

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
