// js/carousel.js — Interactive Project Image Carousel with Auto-Scroll & Translucent Captions

function initCarousels() {
  const carousels = document.querySelectorAll('.carousel-box');

  carousels.forEach((box) => {
    const track = box.querySelector('.carousel-track');
    const slides = box.querySelectorAll('.carousel-slide');
    const prevBtn = box.querySelector('.carousel-btn.prev');
    const nextBtn = box.querySelector('.carousel-btn.next');
    const captionText = box.querySelector('.carousel-caption-text');
    const counter = box.querySelector('.carousel-counter');
    const dotsContainer = box.querySelector('.carousel-dots');

    if (!track || slides.length === 0) return;

    let currentIndex = 0;
    const total = slides.length;
    const delay = parseInt(box.dataset.delay || '3200', 10);
    let autoTimer = null;
    let isHovered = false;
    let isVisible = true;

    // Build dots if container exists
    if (dotsContainer && dotsContainer.children.length === 0) {
      for (let i = 0; i < total; i++) {
        const dot = document.createElement('span');
        dot.className = 'dot' + (i === 0 ? ' active' : '');
        dot.addEventListener('click', (e) => {
          e.stopPropagation();
          goToSlide(i);
        });
        dotsContainer.appendChild(dot);
      }
    }

    const dots = dotsContainer ? dotsContainer.querySelectorAll('.dot') : [];

    function updateCaption(index) {
      const activeSlide = slides[index];
      const cap = activeSlide ? activeSlide.dataset.caption || '' : '';
      if (captionText) {
        captionText.style.opacity = '0';
        setTimeout(() => {
          captionText.textContent = cap;
          captionText.style.opacity = '1';
        }, 150);
      }
      if (counter) {
        const padCur = String(index + 1).padStart(2, '0');
        const padTot = String(total).padStart(2, '0');
        counter.textContent = `[${padCur}/${padTot}]`;
      }
      dots.forEach((d, idx) => {
        d.classList.toggle('active', idx === index);
      });
    }

    function goToSlide(index) {
      if (index < 0) {
        currentIndex = total - 1;
      } else if (index >= total) {
        currentIndex = 0;
      } else {
        currentIndex = index;
      }
      track.style.transform = `translateX(-${currentIndex * 100}%)`;
      updateCaption(currentIndex);
      resetAutoTimer();
    }

    function nextSlide() {
      goToSlide(currentIndex + 1);
    }

    function prevSlide() {
      goToSlide(currentIndex - 1);
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

    // Hover pauses
    box.addEventListener('mouseenter', () => {
      isHovered = true;
      stopAutoTimer();
    });

    box.addEventListener('mouseleave', () => {
      isHovered = false;
      startAutoTimer();
    });

    // Touch swipe support
    let touchStartX = 0;
    let touchEndX = 0;

    box.addEventListener('touchstart', (e) => {
      touchStartX = e.changedTouches[0].screenX;
      isHovered = true;
      stopAutoTimer();
    }, { passive: true });

    box.addEventListener('touchend', (e) => {
      touchEndX = e.changedTouches[0].screenX;
      isHovered = false;
      const diff = touchStartX - touchEndX;
      if (Math.abs(diff) > 40) {
        if (diff > 0) nextSlide();
        else prevSlide();
      }
      startAutoTimer();
    }, { passive: true });

    // Visibility change pauses when switching tabs
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopAutoTimer();
      } else if (isVisible && !isHovered) {
        startAutoTimer();
      }
    });

    // IntersectionObserver — auto-scroll whenever in or near view
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

    // Initial state & immediate start
    updateCaption(0);
    startAutoTimer();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCarousels);
} else {
  initCarousels();
}
