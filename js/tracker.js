/**
 * Huamin Lu Engineering Portfolio - Visitor & Analytics Tracker
 * Features:
 * - Real-time Google Sheet logging for each pageview.
 * - Journey Tracking: Records each project visited and exact dwell time.
 * - Consolidated Recruiter Dossier Email:
 *   1. Triggered immediately when visitor closes or leaves the website.
 *   2. Triggered if visitor remains idle for 10 minutes without activity.
 * - Admin Exclusion: Visit with `?admin=true` to exclude your browser.
 * - Test Mode: Visit with `?test=true` to trigger an immediate test dossier email.
 * - Status Check: Visit with `?admin=status`.
 */

(function () {
  'use strict';

  // --- CONFIGURATION ---
  const GOOGLE_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbxTpGn8MnFj246atHS3uPB4jUy5gi3-gSC60QgGg8o43Ifqdz6Y0A3OENlUG53lxp9i/exec';

  const STORAGE_KEY_ADMIN = 'portfolio_admin_ignore';
  const SESSION_KEY = 'portfolio_session_id';
  const SESSION_START_KEY = 'portfolio_session_start';
  const SESSION_JOURNEY_KEY = 'portfolio_session_journey';
  const SUMMARY_SENT_KEY = 'portfolio_summary_sent';
  const INTERNAL_NAV_KEY = 'portfolio_internal_nav';

  const PROJECT_MAP = {
    'index.html': 'Homepage / Overview',
    'projects.html': 'All Projects Directory',
    'unitree-g1.html': 'Unitree G1 Humanoid',
    'robot-arm.html': '7-DOF QDD Robotic Arm',
    'robot-hand.html': '10-DOF Dexterous Hand',
    'claw-machine.html': 'Miniature Claw Machine',
    'portable-tester.html': 'Portable Battery & Harness Tester',
    'pcb-fixture.html': 'PCB In-Circuit Test Fixture',
    'watarrow-plane.html': 'WatArrow Autonomous UAV',
    'model-car.html': '1:10 Autonomous RC Drift Car',
    'wind-tunnel.html': 'Low-Speed Aero Wind Tunnel',
    'balsa-bridge.html': 'Optimized Balsa Truss Bridge',
    'cryptex-jar.html': 'Mechanical Cryptex Jar',
    'door-locker.html': 'Motorized RFID Deadbolt Locker',
    'g1-arm-recorder.html': 'G1 Arm Trajectory Recorder',
    'g1-hand-recorder.html': 'G1 Hand Teleop Recorder',
    'g1-arm-policy.html': 'G1 Neural MLP Policy',
    'g1-hand-mimic.html': 'G1 Vision Hand Mimic',
    'g1-walk.html': 'G1 Omnidirectional Locomotion',
    'g1-estop.html': 'G1 Wireless Safety E-Stop',
    'g1-mounts.html': 'G1 Sensor Mounts',
    'brookfield-innovation.html': 'Brookfield Pitch',
    'toyota-innovation.html': 'Toyota Innovation Pitch',
    'image-browser.html': 'High-Res Image Browser'
  };

  const pagePath = window.location.pathname.split('/').pop() || 'index.html';
  const currentProjectName = PROJECT_MAP[pagePath] || document.title || 'Engineering Portfolio';

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

  // 1. If Admin flag is set and not test mode, ignore
  if (!isTestMode && localStorage.getItem(STORAGE_KEY_ADMIN) === 'true') {
    return;
  }

  // 2. Ignore local development environments (unless test mode)
  const hostname = window.location.hostname;
  if (!isTestMode && (hostname === 'localhost' || hostname === '127.0.0.1' || window.location.protocol === 'file:')) {
    return;
  }

  // Clear internal navigation flag upon landing
  sessionStorage.removeItem(INTERNAL_NAV_KEY);

  // 3. Session & Journey initialization
  const now = Date.now();
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  let sessionStart = sessionStorage.getItem(SESSION_START_KEY);

  if (!sessionId || isTestMode) {
    sessionId = 's_' + Math.random().toString(36).substring(2, 10) + '_' + now;
    sessionStart = now.toString();
    sessionStorage.setItem(SESSION_KEY, sessionId);
    sessionStorage.setItem(SESSION_START_KEY, sessionStart);
    sessionStorage.setItem(SESSION_JOURNEY_KEY, JSON.stringify([]));
    sessionStorage.removeItem(SUMMARY_SENT_KEY);
  }

  let journey = [];
  try {
    journey = JSON.parse(sessionStorage.getItem(SESSION_JOURNEY_KEY) || '[]');
  } catch (e) {
    journey = [];
  }

  const pageEnterTime = Date.now();

  // Helper duration formatter
  function formatDuration(sec) {
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m + 'm ' + (s > 0 ? s + 's' : '');
  }

  // Determine Device Type
  function getDeviceType() {
    const ua = navigator.userAgent;
    if (/(tablet|ipad|playbook|silk)|(android(?!.*mobi))/i.test(ua)) return 'Tablet';
    if (/Mobile|Android|iP(hone|od)|IEMobile|BlackBerry|Kindle|Silk-Accelerated|(hpw|web)OS|Opera M(obi|ini)/i.test(ua)) return 'Mobile';
    return 'Desktop';
  }

  // Helper notice banner
  function showNotice(msg) {
    function inject() {
      const banner = document.createElement('div');
      banner.textContent = msg;
      banner.style.cssText = [
        'position: fixed', 'bottom: 16px', 'right: 16px', 'background: #111',
        'color: #00ff66', 'font-family: monospace', 'font-size: 12px',
        'padding: 10px 16px', 'border: 1px solid #00ff66', 'border-radius: 4px',
        'z-index: 99999', 'box-shadow: 0 4px 12px rgba(0,0,0,0.5)',
        'pointer-events: none', 'transition: opacity 0.5s ease'
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

  // Intercept internal link clicks so we don't trigger the exit beacon while navigating
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a');
    if (!link || !link.href) return;
    try {
      const url = new URL(link.href, window.location.href);
      if (url.hostname === window.location.hostname) {
        sessionStorage.setItem(INTERNAL_NAV_KEY, 'true');
        // Record current step into journey before navigating
        recordCurrentStep();
      }
    } catch (err) {}
  }, true);

  function recordCurrentStep() {
    const dwellSecs = Math.max(1, Math.round((Date.now() - pageEnterTime) / 1000));
    try {
      const j = JSON.parse(sessionStorage.getItem(SESSION_JOURNEY_KEY) || '[]');
      j.push({
        name: currentProjectName,
        path: pagePath,
        seconds: dwellSecs,
        duration_str: formatDuration(dwellSecs)
      });
      sessionStorage.setItem(SESSION_JOURNEY_KEY, JSON.stringify(j));
    } catch (e) {}
  }

  // Cached Geolocation Data
  let geoCache = null;
  async function getGeoData() {
    if (geoCache) return geoCache;
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 2500);
      const res = await fetch('https://ipwho.is/', { signal: controller.signal });
      clearTimeout(timeoutId);
      if (res.ok) {
        const json = await res.json();
        if (json && json.success) {
          geoCache = {
            ip: json.ip || '',
            city: json.city || 'Unknown',
            region: json.region || 'Unknown',
            country: json.country || 'Unknown',
            isp: (json.connection && json.connection.isp) || 'Unknown',
            org: (json.connection && json.connection.org) || 'Unknown'
          };
          return geoCache;
        }
      }
    } catch (e) {}
    geoCache = { ip: '', city: 'Unknown', region: 'Unknown', country: 'Unknown', isp: 'Unknown', org: 'Unknown' };
    return geoCache;
  }

  // 4. Send Pageview to Google Sheets (Real-time silent log)
  async function logPageview() {
    if (!GOOGLE_SCRIPT_URL || GOOGLE_SCRIPT_URL.includes('YOUR_GOOGLE_APPS_SCRIPT_WEB_APP_URL_HERE')) return;
    const geo = await getGeoData();
    const totalSessionSecs = Math.max(0, Math.round((Date.now() - parseInt(sessionStart || now, 10)) / 1000));

    const payload = {
      type: 'pageview',
      timestamp: new Date().toISOString(),
      local_time: new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true }),
      project_name: currentProjectName,
      page_title: currentProjectName,
      page_path: pagePath,
      dwell_time: '< 5s',
      session_duration: formatDuration(totalSessionSecs),
      referrer: document.referrer || (isTestMode ? 'Manual Test Trigger' : 'Direct / Resume / Bookmark'),
      device: getDeviceType(),
      user_agent: navigator.userAgent,
      screen_res: `${window.screen.width}x${window.screen.height}`,
      session_id: sessionId,
      ip: geo.ip,
      city: geo.city,
      region: geo.region,
      country: geo.country,
      isp: geo.isp,
      org: geo.org
    };

    try {
      await fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        mode: 'no-cors',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify(payload)
      });
    } catch (e) {}
  }

  // 5. Send Consolidated Recruiter Session Dossier
  async function sendSummaryDossier(triggerReason) {
    if (sessionStorage.getItem(SUMMARY_SENT_KEY) === 'true' && !isTestMode) {
      return;
    }
    sessionStorage.setItem(SUMMARY_SENT_KEY, 'true');

    recordCurrentStep();
    let currentJourney = [];
    try {
      currentJourney = JSON.parse(sessionStorage.getItem(SESSION_JOURNEY_KEY) || '[]');
    } catch (e) {
      currentJourney = [];
    }

    // In test mode, populate realistic sample journey if only 1 page
    if (isTestMode && currentJourney.length <= 1) {
      currentJourney = [
        { name: 'Unitree G1 Humanoid', path: 'unitree-g1.html', seconds: 190, duration_str: '3m 10s' },
        { name: '7-DOF QDD Robotic Arm', path: 'robot-arm.html', seconds: 135, duration_str: '2m 15s' },
        { name: '10-DOF Dexterous Hand', path: 'robot-hand.html', seconds: 80, duration_str: '1m 20s' }
      ];
    }

    const geo = await getGeoData();
    const totalSecs = Math.max(1, Math.round((Date.now() - parseInt(sessionStart || now, 10)) / 1000));
    const totalDurationStr = isTestMode ? '6m 45s' : formatDuration(totalSecs);

    const summaryPayload = {
      type: 'session_summary',
      is_test: isTestMode,
      trigger_reason: triggerReason || 'Website closed by visitor',
      session_id: sessionId,
      timestamp: new Date().toISOString(),
      local_time: new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true }),
      total_duration_str: totalDurationStr,
      referrer: document.referrer || (isTestMode ? 'WaterlooWorks' : 'Direct / Resume'),
      device: getDeviceType(),
      screen_res: `${window.screen.width}x${window.screen.height}`,
      city: geo.city,
      region: geo.region,
      country: geo.country,
      isp: geo.isp,
      org: isTestMode ? 'Tesla Inc. / Waterloo' : geo.org,
      journey: currentJourney
    };

    const payloadString = JSON.stringify(summaryPayload);

    if (navigator.sendBeacon) {
      navigator.sendBeacon(GOOGLE_SCRIPT_URL, payloadString);
    } else {
      fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        mode: 'no-cors',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: payloadString,
        keepalive: true
      });
    }

    if (isTestMode) {
      showNotice('🧪 Test Recruiter Dossier sent to your Gmail!');
    }
  }

  // --- TRIGGER 1: ON WEBSITE CLOSE / LEAVE ---
  function onWindowUnload() {
    // If the user clicked an internal link to another page on the portfolio, do not trigger exit
    if (sessionStorage.getItem(INTERNAL_NAV_KEY) === 'true') {
      return;
    }
    // Visitor is actually leaving the site or closing tab
    sendSummaryDossier('Website closed or navigated away');
  }

  window.addEventListener('pagehide', onWindowUnload);
  window.addEventListener('beforeunload', onWindowUnload);

  // --- TRIGGER 2: 10 MINUTES INACTIVITY TIMER ---
  const INACTIVITY_LIMIT_MS = 10 * 60 * 1000; // 10 minutes
  let idleTimer = null;

  function resetIdleTimer() {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      sendSummaryDossier('10 minutes of no activity');
    }, INACTIVITY_LIMIT_MS);
  }

  ['mousemove', 'scroll', 'keydown', 'touchstart', 'click'].forEach(evt => {
    window.addEventListener(evt, resetIdleTimer, { passive: true });
  });
  resetIdleTimer();

  // Run on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      logPageview();
      if (isTestMode) sendSummaryDossier('Manual Test Trigger (?test=true)');
    });
  } else {
    logPageview();
    if (isTestMode) sendSummaryDossier('Manual Test Trigger (?test=true)');
  }
})();
