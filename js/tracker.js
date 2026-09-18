/**
 * Huamin Lu Engineering Portfolio - Universal Visitor & Analytics Tracker
 * Features:
 * - Silent Pageview Logging: Records every page visit to Google Sheets in real time.
 * - Journey Tracking: Accurately times dwell duration on each project page.
 * - Full Summary Email Delivery:
 *   1. Triggered as soon as the visitor closes the website or navigates away.
 *   2. Triggered after 10 minutes of no user activity (idle timeout).
 * - Multi-page awareness: Navigating between portfolio projects does NOT trigger premature exit emails.
 * - Deduplication: Exactly 1 consolidated summary email is sent per session.
 */

(function () {
  'use strict';

  // --- CONFIGURATION ---
  const GOOGLE_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbxTpGn8MnFj246atHS3uPB4jUy5gi3-gSC60QgGg8o43Ifqdz6Y0A3OENlUG53lxp9i/exec';

  const SESSION_KEY = 'portfolio_session_id';
  const SESSION_START_KEY = 'portfolio_session_start';
  const SESSION_JOURNEY_KEY = 'portfolio_session_journey';
  const SUMMARY_SENT_KEY = 'portfolio_summary_sent';
  const INTERNAL_NAV_KEY = 'portfolio_internal_nav';

  // Ignore local file/localhost testing
  const hostname = window.location.hostname;
  if (hostname === 'localhost' || hostname === '127.0.0.1' || window.location.protocol === 'file:') {
    return;
  }

  // Clear internal navigation flag upon landing
  sessionStorage.removeItem(INTERNAL_NAV_KEY);

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

  // Session & Journey initialization
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

  const pageEnterTime = Date.now();

  function formatDuration(sec) {
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m + 'm ' + (s > 0 ? s + 's' : '');
  }

  function getDeviceType() {
    const ua = navigator.userAgent;
    if (/(tablet|ipad|playbook|silk)|(android(?!.*mobi))/i.test(ua)) return 'Tablet';
    if (/Mobile|Android|iP(hone|od)|IEMobile|BlackBerry|Kindle|Silk-Accelerated|(hpw|web)OS|Opera M(obi|ini)/i.test(ua)) return 'Mobile';
    return 'Desktop';
  }

  // Intercept internal link clicks so navigating within portfolio does NOT trigger exit email
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a');
    if (!link || !link.href) return;
    try {
      const url = new URL(link.href, window.location.href);
      if (url.hostname === window.location.hostname) {
        sessionStorage.setItem(INTERNAL_NAV_KEY, 'true');
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

  // Geolocation cache
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

  // 1. Silent Pageview logging to Google Sheets
  async function logPageview() {
    if (!GOOGLE_SCRIPT_URL) return;
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
      referrer: document.referrer || 'Direct / Resume / Bookmark',
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
    } catch (err) {}
  }

  // 2. Consolidated Session Summary Email Delivery
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

    if (currentJourney.length === 0) {
      const elapsed = Math.max(1, Math.round((Date.now() - pageEnterTime) / 1000));
      currentJourney.push({
        name: currentProjectName,
        path: pagePath,
        seconds: elapsed,
        duration_str: formatDuration(elapsed)
      });
    }

    const geo = await getGeoData();
    const totalSecs = Math.max(1, Math.round((Date.now() - parseInt(sessionStart || now, 10)) / 1000));
    const totalDurationStr = formatDuration(totalSecs);

    const summaryPayload = {
      type: 'session_summary',
      is_test: false,
      trigger_reason: triggerReason || 'Website closed or navigated away',
      session_id: sessionId + '_' + Date.now(),
      timestamp: new Date().toISOString(),
      local_time: new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true }),
      project_name: currentProjectName,
      total_duration_str: totalDurationStr,
      session_duration: totalDurationStr,
      referrer: document.referrer || 'Direct / Resume',
      device: getDeviceType(),
      user_agent: navigator.userAgent,
      screen_res: `${window.screen.width}x${window.screen.height}`,
      city: geo.city,
      region: geo.region,
      country: geo.country,
      isp: geo.isp,
      org: geo.org,
      journey: currentJourney
    };

    const payloadString = JSON.stringify(summaryPayload);

    // Use keepalive fetch which survives page close and follows 302 redirects
    try {
      fetch(GOOGLE_SCRIPT_URL, {
        method: 'POST',
        mode: 'no-cors',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: payloadString,
        keepalive: true
      }).catch(() => {});
    } catch (e) {}

    // Fallback beacon
    if (navigator.sendBeacon) {
      try {
        navigator.sendBeacon(GOOGLE_SCRIPT_URL, payloadString);
      } catch (e) {}
    }
  }

  // --- TRIGGER 1: ON WEBSITE CLOSE / NAVIGATE AWAY ---
  function onWindowUnload() {
    if (sessionStorage.getItem(INTERNAL_NAV_KEY) === 'true') {
      return;
    }
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

  ['mousemove', 'scroll', 'keydown', 'touchstart', 'click'].forEach((evt) => {
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
