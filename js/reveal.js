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


  // 5. Global Video Acceleration & Smart Viewport Playback
  // Plays all looping preview videos at crisp 1.5x speed, but ONLY when in view to prevent browser GPU hangs
  (function initVideoSpeed() {
    var targetRate = 1.5;

    // Smart viewport observer: pauses standalone project videos when scrolled out of view vertically
    var videoObserver = null;
    if ("IntersectionObserver" in window) {
      videoObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          var v = entry.target;
          // Skip videos inside the horizontal marquee (.tiles) - those are managed by the section observer
          if (v.closest(".tiles")) return;
          if (entry.isIntersecting) {
            var playPromise = v.play();
            if (playPromise !== undefined) {
              playPromise.catch(function () {});
            }
          } else {
            v.pause();
          }
        });
      }, { rootMargin: "200px 0px" });
    }

    function applyVideoSpeed(v) {
      if (!v || v.dataset.speedInit === "true") return;
      v.dataset.speedInit = "true";

      function setRate() {
        try {
          v.defaultPlaybackRate = targetRate;
          v.playbackRate = targetRate;
        } catch (e) {}
      }

      setRate();
      v.addEventListener("loadedmetadata", setRate, { once: true });
      v.addEventListener("play", setRate);

      // Only observe standalone vertical videos, avoiding continuous thrashing on horizontal marquee tiles
      if (videoObserver && !v.closest(".tiles")) {
        videoObserver.observe(v);
      }
    }

    function scanVideos() {
      document.querySelectorAll("video").forEach(applyVideoSpeed);
    }

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", scanVideos);
    } else {
      scanVideos();
    }

    // Expose so dynamic loaders (like dragscroll) can register new videos cleanly
    window.scanVideos = scanVideos;
  })();

  // 6. Scroll entrance reveal animations
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
