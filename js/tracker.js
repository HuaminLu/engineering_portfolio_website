/**
 * Huamin Lu Engineering Portfolio - Universal Visitor & Analytics Tracker
 * - Tracks 100% of all visits (including your own devices and all outside visitors).
 * - Fires an immediate real-time email alert to luhuaminlu@gmail.com on every visit.
 * - Captures City, Region, Country, Organization/ISP, Project Name, Referrer, and Device.
 * - Logs all visits into the private Google Sheet spreadsheet.
 */

(function () {
  'use strict';

  // --- CONFIGURATION ---
  const GOOGLE_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbxTpGn8MnFj246atHS3uPB4jUy5gi3-gSC60QgGg8o43Ifqdz6Y0A3OENlUG53lxp9i/exec';

  const SESSION_KEY = 'portfolio_session_id';
  const SESSION_START_KEY = 'portfolio_session_start';

  // Clear any legacy admin flags from browser memory
  try {
    localStorage.removeItem('portfolio_admin_ignore');
  } catch (e) {}

  // Ignore local file/localhost testing so it only tracks live web traffic
  const hostname = window.location.hostname;
  if (hostname === 'localhost' || hostname === '127.0.0.1' || window.location.protocol === 'file:') {
    return;
  }

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

  // Session tracking
  const now = Date.now();
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  let sessionStart = sessionStorage.getItem(SESSION_START_KEY);

  if (!sessionId) {
    sessionId = 's_' + Math.random().toString(36).substring(2, 10) + '_' + now;
    sessionStart = now.toString();
    sessionStorage.setItem(SESSION_KEY, sessionId);
    sessionStorage.setItem(SESSION_START_KEY, sessionStart);
  }

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

  // Gather visitor data and transmit
  async function trackVisit() {
    if (!GOOGLE_SCRIPT_URL) return;

    let geoData = { ip: '', city: 'Unknown', region: 'Unknown', country: 'Unknown', isp: 'Unknown', org: 'Unknown' };

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
      // IP lookup fallback
    }

    const totalSessionSecs = Math.max(0, Math.round((Date.now() - parseInt(sessionStart || now, 10)) / 1000));

    const payload = {
      timestamp: new Date().toISOString(),
      local_time: new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true }),
      project_name: currentProjectName,
      page_title: currentProjectName,
      page_path: pagePath,
      page_url: window.location.href,
      session_duration: formatDuration(totalSessionSecs),
      referrer: document.referrer || 'Direct / Resume / Bookmark',
      device: getDeviceType(),
      user_agent: navigator.userAgent,
      screen_res: `${window.screen.width}x${window.screen.height}`,
      session_id: sessionId,
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
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify(payload)
      });
    } catch (err) {
      // Silent fail
    }
  }

  // Execute on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', trackVisit);
  } else {
    trackVisit();
  }
})();
