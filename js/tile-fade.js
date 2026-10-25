// js/tile-fade.js — Cycling crossfade gallery for project preview tiles
// - On projects.html (.grid-tile): Cycles preview images & gifs ONLY when hovered; resets to first main image when not hovered.
// - On index.html (gallery track): Automated continuous cycling via IntersectionObserver.
// - Each gallery exposes a small `_fade` API (park/unpark/syncFrom) so the home
//   marquee (js/dragscroll.js) can stop galleries in off-screen tiles and keep a
//   tile and its clone showing the same frame across the loop seam.

function initFadeGalleries() {
  const galleries = document.querySelectorAll('.fade-gallery');
  if (!galleries.length) return;

  const DEFAULT_INTERVAL = 1800; // default 1.8 seconds per photo for main page scroll
  const HOVER_CYCLE_INTERVAL = 1300; // snappier 1.3s cycle while hovering over a project tile
  const HOVER_START_DELAY = 600; // slight delay before first transition so glancing mouse movements don't flicker

  galleries.forEach((gallery, gIdx) => {
    if (gallery.dataset.fadeInit === 'true') return;
    gallery.dataset.fadeInit = 'true';

    const images = gallery.querySelectorAll('img');
    if (images.length <= 1) return;

    let currentIndex = 0;
    let timer = null;
    let startTimeout = null;
    let nextAt = 0;       // performance.now() of the next scheduled switch (0 = none)
    let parked = false;   // set by the marquee while the tile is off screen
    let inView = true;    // last IntersectionObserver result (index.html mode)

    // Check if this gallery is part of the project catalog (.grid-tile / .project-grid)
    const gridTile = gallery.closest('.grid-tile, .project-grid');
    const isHoverOnly = !!gridTile;

    function showIndex(idx) {
      currentIndex = idx;
      images.forEach((img, i) => {
        if (i === idx) img.classList.add('active');
        else img.classList.remove('active');
      });
    }

    function resetToFirst() {
      stopTimer();
      showIndex(0);
    }

    // Set initial active state
    if (isHoverOnly) {
      resetToFirst();
    } else {
      images.forEach((img, idx) => {
        if (img.classList.contains('active')) currentIndex = idx;
      });
      showIndex(currentIndex);
    }

    function nextImage() {
      showIndex((currentIndex + 1) % images.length);
    }

    function scheduleNext(remainingMs) {
      if (timer) clearTimeout(timer);
      const currentImg = images[currentIndex];
      const customDuration = currentImg ? parseInt(currentImg.dataset.duration, 10) : NaN;
      const baseInterval = isHoverOnly ? HOVER_CYCLE_INTERVAL : DEFAULT_INTERVAL;
      const fullInterval = (!isNaN(customDuration) && customDuration > 0) ? customDuration : baseInterval;
      const currentInterval = (typeof remainingMs === 'number' && remainingMs > 0) ? remainingMs : fullInterval;

      nextAt = performance.now() + currentInterval;
      timer = setTimeout(() => {
        timer = null;
        nextAt = 0;
        nextImage();
        scheduleNext();
      }, currentInterval);
    }

    function startTimer() {
      if (parked) return;
      if (timer || startTimeout) return;
      const staggerDelay = (gIdx % 4) * 350;
      startTimeout = setTimeout(() => {
        startTimeout = null;
        scheduleNext();
      }, staggerDelay);
    }

    function stopTimer() {
      if (startTimeout) {
        clearTimeout(startTimeout);
        startTimeout = null;
      }
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      nextAt = 0;
    }

    if (isHoverOnly) {
      // Hover-only mode for projects page (.grid-tile)
      const hoverTarget = gridTile || gallery;

      hoverTarget.addEventListener('mouseenter', () => {
        stopTimer();
        // Wait briefly before starting the cycle so brief mouse passes don't trigger
        startTimeout = setTimeout(() => {
          startTimeout = null;
          nextImage();
          scheduleNext();
        }, HOVER_START_DELAY);
      });

      hoverTarget.addEventListener('mouseleave', () => {
        resetToFirst();
      });
    } else {
      // Continuous automated cycling for index.html main horizontal track
      if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            inView = entry.isIntersecting;
            if (inView) {
              startTimer();
            } else {
              stopTimer();
            }
          });
        }, { rootMargin: '200px' });
        observer.observe(gallery);
      } else {
        startTimer();
      }
    }

    // Marquee control surface (used by js/dragscroll.js on index.html only)
    gallery._fade = {
      state() {
        return {
          index: currentIndex,
          remaining: nextAt ? Math.max(40, nextAt - performance.now()) : null
        };
      },
      park() {
        parked = true;
        stopTimer();
      },
      unpark() {
        parked = false;
        if (!isHoverOnly && inView) startTimer();
      },
      // Show the same frame as `other` and switch at the same moment it will.
      syncFrom(other) {
        const st = other.state();
        stopTimer();
        showIndex(st.index);
        if (!parked && !isHoverOnly && inView) {
          if (st.remaining !== null) scheduleNext(st.remaining);
          else startTimer();
        }
      }
    };
  });
}

window.initFadeGalleries = initFadeGalleries;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initFadeGalleries);
} else {
  initFadeGalleries();
}
