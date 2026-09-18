/**
 * Huamin Lu Engineering Portfolio - Visitor & Analytics Tracker
 * Features:
 * - Silent, lightweight tracking on page load.
 * - Captures City, Region, Country, Organization/ISP via ipwho.is.
 * - Captures Page, Referrer (WaterlooWorks, Direct, etc.), Device, and Timestamp.
 * - Admin Exclusion: Visit with `?admin=true` to exclude your browser.
 * - Test Mode: Visit with `?test=true` to immediately trigger a test email and sheet row.
 * - Status Check: Visit with `?admin=status` to check your current mode.
 * - Sends data to Google Apps Script Web App for Google Sheets logging and Gmail alerts.
 */

(function () {
  'use strict';

  // --- CONFIGURATION ---
  const GOOGLE_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbxTpGn8MnFj246atHS3uPB4jUy5gi3-gSC60QgGg8o43Ifqdz6Y0A3OENlUG53lxp9i/exec';

  const STORAGE_KEY_ADMIN = 'portfolio_admin_ignore';
  const SESSION_KEY = 'portfolio_session_id';

  // Check URL parameters
  const urlParams = new URLSearchParams(window.location.search);
  const isTestMode = urlParams.has('test');

  // Admin status check
  if (urlParams.get('admin') === 'status') {
    const isExcluded = localStorage.getItem(STORAGE_KEY_ADMIN) === 'true';
    showNotice(isExcluded ? '🛡️ Admin Mode is ON: Your visits are EXCLUDED.' : '✅ Admin Mode is OFF: Your visits are TRACKED.');
    return;
  }

  // Admin enable / disable commands
  if (urlParams.has('admin') || urlParams.has('ignore')) {
    const val = urlParams.get('admin') || urlParams.get('ignore');
    if (val === 'false' || val === 'off') {
      localStorage.removeItem(STORAGE_KEY_ADMIN);
      showNotice('✅ Tracking ENABLED on this device.');
    } else {
      localStorage.setItem(STORAGE_KEY_ADMIN, 'true');
      showNotice('🛡️ Admin Mode: Your visits are EXCLUDED from tracking and email alerts.');
      return;
    }
  }

  // 1. If Admin flag is set and not running explicit test, ignore this visit
  if (!isTestMode && localStorage.getItem(STORAGE_KEY_ADMIN) === 'true') {
    return;
  }

  // 2. Ignore local development environments (unless test mode is forced)
  const hostname = window.location.hostname;
  if (!isTestMode && (hostname === 'localhost' || hostname === '127.0.0.1' || window.location.protocol === 'file:')) {
    return;
  }

  // 3. Session tracking
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  let isNewSession = false;
  if (!sessionId || isTestMode) {
    sessionId = 's_' + Math.random().toString(36).substring(2, 10) + '_' + Date.now();
    sessionStorage.setItem(SESSION_KEY, sessionId);
    isNewSession = true;
  }

  // Determine Device Type
  function getDeviceType() {
    const ua = navigator.userAgent;
    if (/(tablet|ipad|playbook|silk)|(android(?!.*mobi))/i.test(ua)) {
      return 'Tablet';
    }
    if (/Mobile|Android|iP(hone|od)|IEMobile|BlackBerry|Kindle|Silk-Accelerated|(hpw|web)OS|Opera M(obi|ini)/i.test(ua)) {
      return 'Mobile';
    }
    return 'Desktop';
  }

  // Helper notice banner
  function showNotice(msg) {
    function inject() {
      const banner = document.createElement('div');
      banner.textContent = msg;
      banner.style.cssText = [
        'position: fixed',
        'bottom: 16px',
        'right: 16px',
        'background: #111',
        'color: #00ff66',
        'font-family: monospace',
        'font-size: 12px',
        'padding: 10px 16px',
        'border: 1px solid #00ff66',
        'border-radius: 4px',
        'z-index: 99999',
        'box-shadow: 0 4px 12px rgba(0,0,0,0.5)',
        'pointer-events: none',
        'transition: opacity 0.5s ease'
      ].join(';');
      document.body.appendChild(banner);
      setTimeout(() => {
        banner.style.opacity = '0';
        setTimeout(() => banner.remove(), 500);
      }, 4500);
    }
    if (document.readyState === 'loading') {
      window.addEventListener('DOMContentLoaded', inject);
    } else {
      inject();
    }
  }

  // 4. Gather visitor data and transmit
  async function trackVisit() {
    if (!GOOGLE_SCRIPT_URL || GOOGLE_SCRIPT_URL.includes('YOUR_GOOGLE_APPS_SCRIPT_WEB_APP_URL_HERE')) {
      return;
    }

    let geoData = {
      ip: '',
      city: 'Unknown',
      region: 'Unknown',
      country: 'Unknown',
      isp: 'Unknown',
      org: 'Unknown'
    };

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 2500);
      const res = await fetch('https://ipwho.is/', { signal: controller.signal });
      clearTimeout(timeoutId);
      if (res.ok) {
        const json = await res.json();
        if (json && json.success) {
          geoData = {
            ip: json.ip || '',
            city: json.city || 'Unknown',
            region: json.region || 'Unknown',
            country: json.country || 'Unknown',
            isp: (json.connection && json.connection.isp) || 'Unknown',
            org: (json.connection && json.connection.org) || 'Unknown'
          };
        }
      }
    } catch (e) {
      // IP lookup timeout or adblock; proceed with default geoData
    }

    const pagePath = window.location.pathname.split('/').pop() || 'index.html';
    const pageTitle = (isTestMode ? '[TEST] ' : '') + (document.title || 'Engineering Portfolio');

    const payload = {
      timestamp: new Date().toISOString(),
      local_time: new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true }),
      page_title: pageTitle,
      page_path: pagePath,
      page_url: window.location.href,
      referrer: document.referrer || (isTestMode ? 'Manual Test Trigger' : 'Direct / Resume / Bookmark'),
      device: getDeviceType(),
      user_agent: navigator.userAgent,
      screen_res: `${window.screen.width}x${window.screen.height}`,
      session_id: sessionId,
      is_new_session: isNewSession,
      ip: geoData.ip,
      city: geoData.city,
      region: geoData.region,
      country: geoData.country,
      isp: geoData.isp,
      org: geoData.org
    };

    try {
      await fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        mode: 'no-cors',
        headers: {
          'Content-Type': 'text/plain;charset=utf-8'
        },
        body: JSON.stringify(payload)
      });
      if (isTestMode) {
        showNotice('🧪 Test visit sent to your Gmail and Google Sheet!');
      }
    } catch (err) {
      // Fail silently
    }
  }

  // Run on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', trackVisit);
  } else {
    trackVisit();
  }
})();
