// js/tile-fade.js — Automated cycling crossfade gallery for project preview tiles
function initFadeGalleries() {
  const galleries = document.querySelectorAll('.fade-gallery');
  if (!galleries.length) return;

  const DEFAULT_INTERVAL = 1800; // default 1.8 seconds per still photo

  galleries.forEach((gallery, gIdx) => {
    if (gallery.dataset.fadeInit === 'true') return;
    gallery.dataset.fadeInit = 'true';

    const images = gallery.querySelectorAll('img');
    if (images.length <= 1) return;

    let currentIndex = 0;
    // If an image is already active, respect it
    images.forEach((img, idx) => {
      if (img.classList.contains('active')) currentIndex = idx;
    });

    let timer = null;
    let startTimeout = null;

    // Ensure initial active image is set
    images.forEach((img, idx) => {
      if (idx === currentIndex) img.classList.add('active');
      else img.classList.remove('active');
    });

    function nextImage() {
      images[currentIndex].classList.remove('active');
      currentIndex = (currentIndex + 1) % images.length;
      images[currentIndex].classList.add('active');
    }

    function scheduleNext() {
      if (timer) clearTimeout(timer);
      const currentImg = images[currentIndex];
      const customDuration = currentImg ? parseInt(currentImg.dataset.duration, 10) : NaN;
      const currentInterval = (!isNaN(customDuration) && customDuration > 0) ? customDuration : DEFAULT_INTERVAL;

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
  });
}

window.initFadeGalleries = initFadeGalleries;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initFadeGalleries);
} else {
  initFadeGalleries();
}
