// js/tile-fade.js — Automated cycling crossfade gallery for project preview tiles
document.addEventListener('DOMContentLoaded', () => {
  const galleries = document.querySelectorAll('.fade-gallery');
  if (!galleries.length) return;

  const DEFAULT_INTERVAL = 1800; // default 1.8 seconds per still photo

  galleries.forEach((gallery, gIdx) => {
    const images = gallery.querySelectorAll('img');
    if (images.length <= 1) return;

    let currentIndex = 0;
    let timer = null;
    let startTimeout = null;

    // Ensure first image is active initially
    images.forEach((img, idx) => {
      if (idx === 0) img.classList.add('active');
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
      }, { rootMargin: '100px' });
      observer.observe(gallery);
    } else {
      startTimer();
    }
  });
});
