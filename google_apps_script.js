/**
 * Google Apps Script for Huamin Lu's Engineering Portfolio Analytics
 * 
 * FEATURES:
 * - Consolidated Recruiter Session Dossier: Sends 1 comprehensive summary email
 *   when a visitor closes the website or after 10 minutes of inactivity.
 * - Journey Breakdown: Lists every project page viewed and exact dwell time (seconds/minutes).
 * - Custom Pie/Doughnut Chart: Visualizes exactly which projects this specific visitor focused on.
 * - Real-time Google Sheet logging for every pageview.
 * - Prevents duplicate emails using session deduplication.
 * 
 * TO UPDATE YOUR APPS SCRIPT:
 * 1. Go to https://script.google.com and open your "Portfolio Analytics" project.
 * 2. Replace all code in Code.gs with this file and save (Ctrl+S).
 * 3. Click "Deploy" -> "Manage deployments".
 * 4. Click the pencil icon (Edit) on your Web app deployment.
 * 5. Under "Version", choose "New version".
 * 6. Click "Deploy".
 */

// Configuration
const RECIPIENT_EMAIL = 'luhuaminlu@gmail.com';
const SPREADSHEET_NAME = 'Portfolio Visitor Analytics';

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return ContentService.createTextOutput(JSON.stringify({ status: "empty" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const data = JSON.parse(e.postData.contents);
    const ss = getOrCreateSpreadsheet();
    const sheet = getOrCreateSheet(ss);

    // 1. PAGEVIEW EVENT: Log row into Google Sheets silently in real time
    if (data.type === 'pageview') {
      sheet.appendRow([
        data.local_time || new Date().toISOString(),
        data.city || 'Unknown',
        data.region || 'Unknown',
        data.country || 'Unknown',
        data.org || data.isp || 'Unknown',
        data.project_name || data.page_title || 'Engineering Portfolio',
        data.page_path || 'index.html',
        data.dwell_time || '< 5s',
        data.session_duration || '< 1m',
        data.referrer || 'Direct / Bookmark',
        data.device || 'Unknown',
        data.screen_res || 'Unknown',
        data.ip || 'Hidden',
        data.session_id || 'Unknown'
      ]);

      return ContentService.createTextOutput(JSON.stringify({ status: "logged" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // 2. SESSION SUMMARY EVENT: Sent when user closes website or after 10 min inactivity
    if (data.type === 'session_summary') {
      // Deduplicate: Ensure only 1 summary email per visitor session
      const cache = CacheService.getScriptCache();
      const cacheKey = 'sent_summary_' + (data.session_id || 'unknown');
      if (cache.get(cacheKey) && !data.is_test) {
        return ContentService.createTextOutput(JSON.stringify({ status: "duplicate_skipped" }))
          .setMimeType(ContentService.MimeType.JSON);
      }
      cache.put(cacheKey, 'true', 3600); // lock for 1 hour

      sendSessionSummaryEmail(data, ss.getUrl());

      return ContentService.createTextOutput(JSON.stringify({ status: "summary_sent" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    return ContentService.createTextOutput(JSON.stringify({ status: "ignored" }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function sendSessionSummaryEmail(data, sheetUrl) {
  const loc = [data.city, data.region, data.country].filter(Boolean).join(', ') || 'Unknown Location';
  const org = data.org && data.org !== 'Unknown' ? data.org : (data.isp || 'Unknown Network');
  const ref = data.referrer || 'Direct / Resume';
  const totalDuration = data.total_duration_str || '< 1m';
  const journey = data.journey || [];
  const triggerReason = data.trigger_reason || 'Website closed by visitor';

  const isTest = data.is_test;
  const prefix = isTest ? '🧪 [TEST DOSSIER] ' : '📋 ';
  const subject = `${prefix}Recruiter Dossier: ${loc} — ${totalDuration} (${ref})`;

  // Calculate project durations for chart
  const projectTimes = {};
  let totalTimeSecs = 0;

  journey.forEach(step => {
    const name = step.name || 'Overview';
    const secs = parseInt(step.seconds || 5, 10);
    projectTimes[name] = (projectTimes[name] || 0) + secs;
    totalTimeSecs += secs;
  });

  const chartProjects = Object.keys(projectTimes).map(name => ({
    name: name,
    seconds: projectTimes[name],
    percent: totalTimeSecs > 0 ? Math.round((projectTimes[name] / totalTimeSecs) * 100) : 0,
    timeStr: formatDuration(projectTimes[name])
  })).sort((a, b) => b.seconds - a.seconds);

  // Generate QuickChart URL
  const chartUrl = generateVisitorChartUrl(chartProjects);

  // Colors for charts and meters
  const colors = ['#2ea043', '#58a6ff', '#a371f7', '#f0883e', '#d29922', '#388bfd', '#db61a2'];

  // Build Chronological Journey HTML
  let journeyHtml = '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">';
  journey.forEach((step, idx) => {
    journeyHtml += `
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 8px 0; color: #888; width: 32px; font-family: monospace;">#${idx + 1}</td>
        <td style="padding: 8px 6px; color: #111; font-weight: 500;">
          ${step.name}
          <div style="font-size: 11px; color: #777;">${step.path}</div>
        </td>
        <td style="padding: 8px 0; text-align: right; color: #0969da; font-weight: bold; font-family: monospace;">
          ⏱️ ${step.duration_str || '< 5s'}
        </td>
      </tr>`;
  });
  journeyHtml += '</table>';

  // Build Project Breakdown Progress Bars
  let breakdownHtml = '<table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px;">';
  chartProjects.forEach((p, idx) => {
    const col = colors[idx % colors.length];
    breakdownHtml += `
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 6px 0; width: 44%; color: #222; font-weight: 500;">${p.name}</td>
        <td style="padding: 6px 8px; width: 36%;">
          <div style="background: #f0f0f0; border-radius: 4px; overflow: hidden; height: 11px; width: 100%;">
            <div style="background: ${col}; width: ${p.percent}%; height: 100%;"></div>
          </div>
        </td>
        <td style="padding: 6px 0; width: 20%; text-align: right; color: #555; font-family: monospace; font-size: 12px;">
          ${p.percent}% (${p.timeStr})
        </td>
      </tr>`;
  });
  breakdownHtml += '</table>';

  const htmlBody = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; border: 1px solid #e1e4e8; border-radius: 8px; background-color: #ffffff;">
      
      <!-- Top Banner -->
      <div style="background-color: #0d1117; padding: 16px 20px; border-radius: 6px; margin-bottom: 20px;">
        <h2 style="color: #58a6ff; margin: 0; font-size: 17px; font-family: monospace;">
          ⚡ ${isTest ? 'TEST RECRUITER DOSSIER' : 'RECRUITER BROWSING DOSSIER'}
        </h2>
        <div style="color: #8b949e; font-size: 12px; margin-top: 4px; font-family: monospace;">
          Trigger: ${triggerReason} • Total Pages: ${journey.length}
        </div>
      </div>

      <!-- Core Session Metrics -->
      <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 22px;">
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666; width: 160px;">⏳ <strong>Total Time on Portfolio</strong></td>
          <td style="padding: 9px 0; color: #238636; font-size: 16px; font-weight: bold;">${totalDuration}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">📍 <strong>Location</strong></td>
          <td style="padding: 9px 0; color: #111;"><strong>${loc}</strong></td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">🏢 <strong>Company / Network</strong></td>
          <td style="padding: 9px 0; color: #111;">${org}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">🔗 <strong>Referrer</strong></td>
          <td style="padding: 9px 0; color: #111;"><strong>${ref}</strong></td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">📱 <strong>Device</strong></td>
          <td style="padding: 9px 0; color: #111;">${data.device} (${data.screen_res})</td>
        </tr>
        <tr>
          <td style="padding: 9px 0; color: #666;">⏰ <strong>Session Time (EDT)</strong></td>
          <td style="padding: 9px 0; color: #111;">${data.local_time}</td>
        </tr>
      </table>

      <!-- Visual Attention Chart -->
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; margin-bottom: 22px;">
        <h3 style="margin: 0 0 12px 0; font-size: 15px; color: #1e293b; font-family: monospace;">
          🥧 VISITOR ATTENTION BY PROJECT
        </h3>
        ${chartUrl ? `<div style="text-align: center; margin-bottom: 15px;"><img src="${chartUrl}" alt="Visitor Interest Chart" style="max-width: 100%; height: auto; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);"></div>` : ''}
        ${breakdownHtml}
      </div>

      <!-- Chronological Journey Steps -->
      <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; margin-bottom: 22px;">
        <h3 style="margin: 0 0 12px 0; font-size: 15px; color: #1e293b; font-family: monospace;">
          🧭 STEP-BY-STEP BROWSING JOURNEY
        </h3>
        ${journeyHtml}
      </div>

      <!-- Action Button -->
      <div style="text-align: center; margin: 25px 0 10px 0;">
        <a href="${sheetUrl}" style="background-color: #238636; color: #ffffff; padding: 11px 22px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 14px;">
          📊 Open Visitor Log in Google Sheets
        </a>
      </div>
    </div>
  `;

  MailApp.sendEmail({
    to: RECIPIENT_EMAIL,
    subject: subject,
    htmlBody: htmlBody
  });
}

function generateVisitorChartUrl(projects) {
  if (!projects || projects.length === 0) return '';
  const labels = projects.map(p => {
    let n = p.name.replace(' (Unitree G1)', '').replace(' - Huamin Lu', '');
    return n.length > 18 ? n.substring(0, 16) + '..' : n;
  });
  const data = projects.map(p => p.seconds);
  const colors = ['#2ea043', '#58a6ff', '#a371f7', '#f0883e', '#d29922', '#388bfd', '#db61a2'];

  const chartConfig = {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: data,
        backgroundColor: colors.slice(0, data.length),
        borderWidth: 2
      }]
    },
    options: {
      legend: { position: 'right', labels: { fontSize: 11, boxWidth: 12 } },
      title: { display: true, text: 'Time Spent Per Project', fontSize: 13 }
    }
  };

  return 'https://quickchart.io/chart?c=' + encodeURIComponent(JSON.stringify(chartConfig)) + '&w=520&h=230&bkg=white';
}

function formatDuration(sec) {
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + 'm ' + (s > 0 ? s + 's' : '');
}

function getOrCreateSpreadsheet() {
  try {
    const active = SpreadsheetApp.getActiveSpreadsheet();
    if (active) return active;
  } catch (e) {}

  const files = DriveApp.getFilesByName(SPREADSHEET_NAME);
  if (files.hasNext()) {
    return SpreadsheetApp.open(files.next());
  } else {
    return SpreadsheetApp.create(SPREADSHEET_NAME);
  }
}

function getOrCreateSheet(ss) {
  let sheet = ss.getSheetByName('Visitors');
  if (!sheet) {
    sheet = ss.insertSheet('Visitors');
    const defaultSheet = ss.getSheetByName('Sheet1');
    if (defaultSheet && ss.getSheets().length > 1) {
      ss.deleteSheet(defaultSheet);
    }
    const headers = [
      'Timestamp (EDT)', 'City', 'Region / State', 'Country', 
      'Network / ISP', 'Project / Page Title', 'Page Path', 'Dwell Time',
      'Session Duration', 'Referrer', 'Device', 'Screen Res', 'IP', 'Session ID'
    ];
    sheet.appendRow(headers);
    const headerRange = sheet.getRange(1, 1, 1, headers.length);
    headerRange.setBackground('#0d1117');
    headerRange.setFontColor('#ffffff');
    headerRange.setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.setRowHeight(1, 32);
  }
  return sheet;
}
