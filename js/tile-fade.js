// js/tile-fade.js — Cycling crossfade gallery for project preview tiles
// - On projects.html (.grid-tile): Cycles preview images & gifs ONLY when hovered; resets to first main image when not hovered.
// - On index.html (gallery track): Automated continuous cycling via IntersectionObserver.

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

    // Check if this gallery is part of the project catalog (.grid-tile / .project-grid)
    const gridTile = gallery.closest('.grid-tile, .project-grid');
    const isHoverOnly = !!gridTile;

    function resetToFirst() {
      stopTimer();
      currentIndex = 0;
      images.forEach((img, idx) => {
        if (idx === 0) img.classList.add('active');
        else img.classList.remove('active');
      });
    }

    // Set initial active state
    if (isHoverOnly) {
      resetToFirst();
    } else {
      images.forEach((img, idx) => {
        if (img.classList.contains('active')) currentIndex = idx;
      });
      images.forEach((img, idx) => {
        if (idx === currentIndex) img.classList.add('active');
        else img.classList.remove('active');
      });
    }

    function nextImage() {
      images[currentIndex].classList.remove('active');
      currentIndex = (currentIndex + 1) % images.length;
      images[currentIndex].classList.add('active');
    }

    function scheduleNext() {
      if (timer) clearTimeout(timer);
      const currentImg = images[currentIndex];
      const customDuration = currentImg ? parseInt(currentImg.dataset.duration, 10) : NaN;
      const baseInterval = isHoverOnly ? HOVER_CYCLE_INTERVAL : DEFAULT_INTERVAL;
      const currentInterval = (!isNaN(customDuration) && customDuration > 0) ? customDuration : baseInterval;

      timer = setTimeout(() => {
        nextImage();
        scheduleNext();
      }, currentInterval);
    }

    function startTimer() {
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
            if (entry.isIntersecting) {
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
  });
}

window.initFadeGalleries = initFadeGalleries;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initFadeGalleries);
} else {
  initFadeGalleries();
}
