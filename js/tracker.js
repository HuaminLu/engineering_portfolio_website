/**
 * Huamin Lu Engineering Portfolio - Visitor & Analytics Tracker
 * Features:
 * - Silent, lightweight tracking on page load.
 * - Exact project-level identification (Unitree G1, 7-DOF Arm, 10-DOF Hand, etc.).
 * - Duration tracking: tracks time spent on each project page and total session time.
 * - Admin Exclusion: Visit with `?admin=true` to exclude your browser.
 * - Test Mode: Visit with `?test=true` to trigger an immediate test alert with live charts.
 * - Status Check: Visit with `?admin=status` to check your current mode.
 * - Sends data to Google Apps Script Web App for Google Sheets logging and Gmail alerts.
 */

(function () {
  'use strict';

  // --- CONFIGURATION ---
  const GOOGLE_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbxTpGn8MnFj246atHS3uPB4jUy5gi3-gSC60QgGg8o43Ifqdz6Y0A3OENlUG53lxp9i/exec';

  const STORAGE_KEY_ADMIN = 'portfolio_admin_ignore';
  const SESSION_KEY = 'portfolio_session_id';
  const SESSION_START_KEY = 'portfolio_session_start';
  const PREV_PAGE_KEY = 'portfolio_prev_page';
  const PREV_TIME_KEY = 'portfolio_prev_time';

  // Project lookup
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

  // 1. If Admin flag is set and not running explicit test, ignore this visit
  if (!isTestMode && localStorage.getItem(STORAGE_KEY_ADMIN) === 'true') {
    return;
  }

  // 2. Ignore local development environments (unless test mode is forced)
  const hostname = window.location.hostname;
  if (!isTestMode && (hostname === 'localhost' || hostname === '127.0.0.1' || window.location.protocol === 'file:')) {
    return;
  }

  // 3. Session & Duration tracking
  const now = Date.now();
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  let sessionStart = sessionStorage.getItem(SESSION_START_KEY);
  let isNewSession = false;

  if (!sessionId || isTestMode) {
    sessionId = 's_' + Math.random().toString(36).substring(2, 10) + '_' + now;
    sessionStart = now.toString();
    sessionStorage.setItem(SESSION_KEY, sessionId);
    sessionStorage.setItem(SESSION_START_KEY, sessionStart);
    isNewSession = true;
  }

  // Calculate session duration so far
  const totalSessionSecs = Math.max(0, Math.round((now - parseInt(sessionStart || now, 10)) / 1000));
  const sessionDurationStr = formatDuration(totalSessionSecs);

  // Calculate previous page dwell time
  const prevPage = sessionStorage.getItem(PREV_PAGE_KEY);
  const prevTime = sessionStorage.getItem(PREV_TIME_KEY);
  let prevPageInfo = 'Session Start';

  if (prevPage && prevTime) {
    const dwellSecs = Math.max(1, Math.round((now - parseInt(prevTime, 10)) / 1000));
    prevPageInfo = `${prevPage} (${formatDuration(dwellSecs)})`;
  }

  // Record current page enter time for next navigation
  const pageEnterTime = now;
  sessionStorage.setItem(PREV_PAGE_KEY, currentProjectName);
  sessionStorage.setItem(PREV_TIME_KEY, pageEnterTime.toString());

  // Listen for page exit / navigation to update exit duration
  function onPageExit() {
    const elapsedSecs = Math.max(1, Math.round((Date.now() - pageEnterTime) / 1000));
    const durationStr = formatDuration(elapsedSecs);
    sessionStorage.setItem(PREV_TIME_KEY, pageEnterTime.toString());

    if (navigator.sendBeacon && GOOGLE_SCRIPT_URL) {
      const exitData = JSON.stringify({
        type: 'exit',
        session_id: sessionId,
        project_name: currentProjectName,
        duration_str: durationStr
      });
      navigator.sendBeacon(GOOGLE_SCRIPT_URL, exitData);
    }
  }

  window.addEventListener('pagehide', onPageExit);
  window.addEventListener('beforeunload', onPageExit);

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

    const pageTitle = (isTestMode ? '[TEST] ' : '') + currentProjectName;

    const payload = {
      timestamp: new Date().toISOString(),
      local_time: new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true }),
      project_name: currentProjectName,
      page_title: pageTitle,
      page_path: pagePath,
      page_url: window.location.href,
      prev_page_info: prevPageInfo,
      session_duration: sessionDurationStr,
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
