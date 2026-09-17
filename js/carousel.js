// js/carousel.js — Interactive Project Image Carousel with Fast Slide Transition & Continuous Auto-Scroll

function initCarousels() {
  const carousels = document.querySelectorAll('.carousel-box');

  carousels.forEach((box) => {
    if (box.dataset.carouselInit === 'true') return;
    box.dataset.carouselInit = 'true';

    const track = box.querySelector('.carousel-track');
    const origSlides = Array.from(box.querySelectorAll('.carousel-slide'));
    const prevBtn = box.querySelector('.carousel-btn.prev');
    const nextBtn = box.querySelector('.carousel-btn.next');
    const captionText = box.querySelector('.carousel-caption-text');
    const counter = box.querySelector('.carousel-counter');
    const dotsContainer = box.querySelector('.carousel-dots');

    if (!track || origSlides.length === 0) return;

    const total = origSlides.length;
    // Slide duration configurable via data-delay attribute (defaults to 2000ms)
    const delay = parseInt(box.dataset.delay, 10) || 2000;
    const TRANSITION_STYLE = 'transform 0.35s cubic-bezier(0.25, 0.1, 0.25, 1.0)';

    let autoTimer = null;
    let isVisible = false;
    let isTransitioning = false;
    let transitionFallbackTimer = null;

    let currentIndex = 1; // Start at real slide 1 (slide 0 is clone of last)

    if (total > 1) {
      // Clone first and last slides for seamless infinite loop
      const firstClone = origSlides[0].cloneNode(true);
      const lastClone = origSlides[total - 1].cloneNode(true);
      firstClone.classList.add('clone');
      lastClone.classList.add('clone');

      track.appendChild(firstClone);
      track.insertBefore(lastClone, origSlides[0]);
    } else {
      currentIndex = 0;
    }

    // Set initial position
    track.style.transition = 'none';
    track.style.transform = `translateX(-${currentIndex * 100}%)`;
    void track.offsetWidth;
    track.style.transition = TRANSITION_STYLE;

    // Build dots (only for real slides)
    if (dotsContainer && dotsContainer.children.length === 0) {
      for (let i = 0; i < total; i++) {
        const dot = document.createElement('span');
        dot.className = 'dot' + (i === 0 ? ' active' : '');
        dot.addEventListener('click', (e) => {
          e.stopPropagation();
          goToRealSlide(i);
        });
        dotsContainer.appendChild(dot);
      }
    }

    const dots = dotsContainer ? dotsContainer.querySelectorAll('.dot') : [];

    function getRealIndex(idx) {
      if (total <= 1) return 0;
      if (idx === 0) return total - 1;
      if (idx === total + 1) return 0;
      return idx - 1;
    }

    function updateCaption(idx) {
      const realIdx = getRealIndex(idx);
      const activeSlide = origSlides[realIdx];
      const cap = activeSlide ? activeSlide.dataset.caption || '' : '';
      if (captionText) {
        captionText.textContent = cap;
      }
      if (counter) {
        const padCur = String(realIdx + 1).padStart(2, '0');
        const padTot = String(total).padStart(2, '0');
        counter.textContent = `[${padCur}/${padTot}]`;
      }
      dots.forEach((d, i) => {
        d.classList.toggle('active', i === realIdx);
      });
    }

    function onTransitionDone() {
      clearTimeout(transitionFallbackTimer);
      isTransitioning = false;
      if (total > 1) {
        if (currentIndex >= total + 1) {
          track.style.transition = 'none';
          currentIndex = 1;
          track.style.transform = `translateX(-${currentIndex * 100}%)`;
          void track.offsetWidth;
        } else if (currentIndex <= 0) {
          track.style.transition = 'none';
          currentIndex = total;
          track.style.transform = `translateX(-${currentIndex * 100}%)`;
          void track.offsetWidth;
        }
      }
    }

    track.addEventListener('transitionend', onTransitionDone);

    function moveTo(index, animate = true) {
      currentIndex = index;
      clearTimeout(transitionFallbackTimer);
      if (animate) {
        track.style.transition = TRANSITION_STYLE;
        isTransitioning = true;
        transitionFallbackTimer = setTimeout(onTransitionDone, 400);
      } else {
        track.style.transition = 'none';
      }
      track.style.transform = `translateX(-${currentIndex * 100}%)`;
      updateCaption(currentIndex);
    }

    function nextSlide() {
      if (total <= 1) return;
      moveTo(currentIndex + 1, true);
      resetAutoTimer();
    }

    function prevSlide() {
      if (total <= 1) return;
      moveTo(currentIndex - 1, true);
      resetAutoTimer();
    }

    function goToRealSlide(realIdx) {
      moveTo(total > 1 ? realIdx + 1 : realIdx, true);
      resetAutoTimer();
    }

    if (nextBtn) {
      nextBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        nextSlide();
      });
    }

    if (prevBtn) {
      prevBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        prevSlide();
      });
    }

    // Auto-scroll loop — continuously sliding when in view (supports per-slide data-duration or container data-delay)
    function getSlideDelay() {
      const realIdx = getRealIndex(currentIndex);
      const activeSlide = origSlides[realIdx];
      if (activeSlide && activeSlide.dataset.duration) {
        return parseInt(activeSlide.dataset.duration, 10) || delay;
      }
      return delay;
    }

    function startAutoTimer() {
      if (autoTimer || !isVisible || total <= 1) return;
      autoTimer = setTimeout(() => {
        autoTimer = null;
        nextSlide();
      }, getSlideDelay());
    }

    function stopAutoTimer() {
      if (autoTimer) {
        clearTimeout(autoTimer);
        autoTimer = null;
      }
    }

    function resetAutoTimer() {
      stopAutoTimer();
      startAutoTimer();
    }

    // --- Pointer / Mouse Drag & Touch Swipe Support ---
    let isDragging = false;
    let startX = 0;
    let currentX = 0;
    let dragDist = 0;

    function onPointerDown(clientX) {
      if (total <= 1) return;
      isDragging = true;
      startX = clientX;
      currentX = clientX;
      dragDist = 0;
      stopAutoTimer();
      track.style.transition = 'none';
      box.classList.add('dragging');
    }

    function onPointerMove(clientX) {
      if (!isDragging) return;
      currentX = clientX;
      dragDist = currentX - startX;
      const boxWidth = box.offsetWidth || 1;
      const percentOffset = (dragDist / boxWidth) * 100;
      track.style.transform = `translateX(calc(-${currentIndex * 100}% + ${percentOffset}%))`;
    }

    function onPointerUp() {
      if (!isDragging) return;
      isDragging = false;
      box.classList.remove('dragging');
      const boxWidth = box.offsetWidth || 1;
      const threshold = boxWidth * 0.12;

      if (dragDist < -threshold) {
        moveTo(currentIndex + 1, true);
      } else if (dragDist > threshold) {
        moveTo(currentIndex - 1, true);
      } else {
        moveTo(currentIndex, true);
      }
      resetAutoTimer();
    }

    // Mouse drag events
    box.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest('.carousel-btn') || e.target.closest('.dot')) return;
      e.preventDefault();
      onPointerDown(e.clientX);
    });

    window.addEventListener('mousemove', (e) => {
      if (isDragging) {
        e.preventDefault();
        onPointerMove(e.clientX);
      }
    });

    window.addEventListener('mouseup', () => {
      if (isDragging) {
        onPointerUp();
      }
    });

    // Touch events with vertical scroll discrimination
    let touchStartX = 0;
    let touchStartY = 0;
    let isTouchHorizontal = false;
    let isTouchActive = false;

    box.addEventListener('touchstart', (e) => {
      if (total <= 1 || e.touches.length !== 1) return;
      isTouchActive = true;
      isTouchHorizontal = false;
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
      dragDist = 0;
      stopAutoTimer();
    }, { passive: true });

    box.addEventListener('touchmove', (e) => {
      if (!isTouchActive || e.touches.length !== 1) return;
      const touchX = e.touches[0].clientX;
      const touchY = e.touches[0].clientY;
      const dx = touchX - touchStartX;
      const dy = touchY - touchStartY;

      if (!isTouchHorizontal) {
        if (Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) {
          isTouchHorizontal = true;
          isDragging = true;
          startX = touchStartX;
          box.classList.add('dragging');
          track.style.transition = 'none';
        } else if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {
          isTouchActive = false;
          isDragging = false;
          resetAutoTimer();
          return;
        }
      }

      if (isDragging) {
        currentX = touchX;
        dragDist = currentX - startX;
        const boxWidth = box.offsetWidth || 1;
        const percentOffset = (dragDist / boxWidth) * 100;
        track.style.transform = `translateX(calc(-${currentIndex * 100}% + ${percentOffset}%))`;
      }
    }, { passive: true });

    box.addEventListener('touchend', () => {
      if (isDragging) {
        onPointerUp();
      } else {
        isTouchActive = false;
        resetAutoTimer();
      }
    }, { passive: true });

    box.addEventListener('touchcancel', () => {
      if (isDragging) {
        onPointerUp();
      } else {
        isTouchActive = false;
        resetAutoTimer();
      }
    }, { passive: true });

    // Tab visibility
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopAutoTimer();
      } else if (isVisible) {
        startAutoTimer();
      }
    });

    // "only start when user is close to that section of the page"
    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            isVisible = true;
            startAutoTimer();
          } else {
            isVisible = false;
            stopAutoTimer();
          }
        });
      }, { threshold: 0.05, rootMargin: '200px 0px' });
      observer.observe(box);
    } else {
      isVisible = true;
      startAutoTimer();
    }

    // Initial caption update
    updateCaption(currentIndex);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCarousels);
} else {
  initCarousels();
}
