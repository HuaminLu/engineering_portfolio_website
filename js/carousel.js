// js/carousel.js — Interactive Project Image Carousel with Auto-Scroll, Mouse Drag, Infinite Wrap & Translucent Captions

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
    const delay = parseInt(box.dataset.delay || '3500', 10);
    let autoTimer = null;
    let isHovered = false;
    let isVisible = true;
    let isTransitioning = false;

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
    track.style.transition = 'transform 0.45s cubic-bezier(0.25, 1, 0.5, 1)';

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
        captionText.style.opacity = '0';
        setTimeout(() => {
          captionText.textContent = cap;
          captionText.style.opacity = '1';
        }, 120);
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

    function moveTo(index, animate = true) {
      currentIndex = index;
      if (animate) {
        track.style.transition = 'transform 0.45s cubic-bezier(0.25, 1, 0.5, 1)';
        isTransitioning = true;
      } else {
        track.style.transition = 'none';
      }
      track.style.transform = `translateX(-${currentIndex * 100}%)`;
      updateCaption(currentIndex);
    }

    function nextSlide() {
      if (total <= 1 || isTransitioning) return;
      moveTo(currentIndex + 1, true);
      resetAutoTimer();
    }

    function prevSlide() {
      if (total <= 1 || isTransitioning) return;
      moveTo(currentIndex - 1, true);
      resetAutoTimer();
    }

    function goToRealSlide(realIdx) {
      if (isTransitioning) return;
      moveTo(total > 1 ? realIdx + 1 : realIdx, true);
      resetAutoTimer();
    }

    // Seamless loop reset on transitionend
    track.addEventListener('transitionend', () => {
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
    });

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

    // Auto-scroll loop
    function startAutoTimer() {
      if (autoTimer || !isVisible || isHovered || total <= 1) return;
      autoTimer = setInterval(() => {
        nextSlide();
      }, delay);
    }

    function stopAutoTimer() {
      if (autoTimer) {
        clearInterval(autoTimer);
        autoTimer = null;
      }
    }

    function resetAutoTimer() {
      stopAutoTimer();
      startAutoTimer();
    }

    // Hover pauses auto-scroll
    box.addEventListener('mouseenter', () => {
      isHovered = true;
      stopAutoTimer();
    });

    box.addEventListener('mouseleave', () => {
      isHovered = false;
      startAutoTimer();
    });

    // --- Pointer / Mouse Drag & Touch Swipe Support ---
    let isDragging = false;
    let startX = 0;
    let currentX = 0;
    let dragDist = 0;

    function onPointerDown(clientX) {
      if (total <= 1 || isTransitioning) return;
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
      const threshold = boxWidth * 0.12; // 12% swipe threshold

      if (dragDist < -threshold) {
        moveTo(currentIndex + 1, true);
      } else if (dragDist > threshold) {
        moveTo(currentIndex - 1, true);
      } else {
        moveTo(currentIndex, true);
      }
      startAutoTimer();
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

    // Touch events
    box.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        onPointerDown(e.touches[0].clientX);
      }
    }, { passive: true });

    box.addEventListener('touchmove', (e) => {
      if (isDragging && e.touches.length === 1) {
        onPointerMove(e.touches[0].clientX);
      }
    }, { passive: true });

    box.addEventListener('touchend', () => {
      if (isDragging) {
        onPointerUp();
      }
    }, { passive: true });

    // Tab visibility
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopAutoTimer();
      } else if (isVisible && !isHovered) {
        startAutoTimer();
      }
    });

    // IntersectionObserver
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
      }, { threshold: 0.05, rootMargin: '80px 0px' });
      observer.observe(box);
    } else {
      isVisible = true;
      startAutoTimer();
    }

    // Initial caption update & start
    updateCaption(currentIndex);
    startAutoTimer();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCarousels);
} else {
  initCarousels();
}
