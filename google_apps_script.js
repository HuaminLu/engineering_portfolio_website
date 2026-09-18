/**
 * Google Apps Script for Huamin Lu's Engineering Portfolio Analytics
 * 
 * FEATURES:
 * - Real-Time Instant Email Alert on EVERY website/project visit.
 * - Exact project name identification (Unitree G1, Robot Arm, Robot Hand, etc.).
 * - Visitor Geolocation: City, Region, Country, Organization/ISP (e.g. Tesla, Apple, UW).
 * - Embedded Pie / Doughnut Chart & HTML Bar Graph of Most Viewed Projects.
 * - Auto-appends every visit to private Google Sheet "Portfolio Visitor Analytics".
 * 
 * TO UPDATE YOUR SCRIPT (30 seconds):
 * 1. Open https://script.google.com and open your "Portfolio Analytics" project.
 * 2. Erase everything in Code.gs, paste this file, and press Ctrl+S.
 * 3. Click "Deploy" -> "Manage deployments".
 * 4. Click the pencil icon (Edit) -> choose "New version" -> click "Deploy".
 */

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

    // 1. Log visit to Google Sheet
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

    // 2. Query project stats across the sheet for chart
    const stats = getProjectStats(sheet);

    // 3. Send email alert immediately
    sendEmailNotification(data, ss.getUrl(), stats);

    return ContentService.createTextOutput(JSON.stringify({ status: "email_sent_and_logged" }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function getProjectStats(sheet) {
  const data = sheet.getDataRange().getValues();
  const counts = {};
  let totalViews = 0;

  for (let i = 1; i < data.length; i++) {
    const project = data[i][5];
    if (project && typeof project === 'string' && !project.startsWith('[TEST]')) {
      counts[project] = (counts[project] || 0) + 1;
      totalViews++;
    }
  }

  const sorted = Object.keys(counts).map(name => ({
    name: name,
    count: counts[name],
    percent: totalViews > 0 ? Math.round((counts[name] / totalViews) * 100) : 0
  })).sort((a, b) => b.count - a.count);

  return { top: sorted.slice(0, 6), total: totalViews };
}

function generateVisitorChartUrl(topProjects, total) {
  if (!topProjects || topProjects.length === 0) return '';
  const labels = topProjects.map(p => {
    let n = p.name.replace(' (Unitree G1)', '').replace(' - Huamin Lu', '');
    return n.length > 18 ? n.substring(0, 16) + '..' : n;
  });
  const data = topProjects.map(p => p.count);
  const colors = ['#2ea043', '#58a6ff', '#a371f7', '#f0883e', '#d29922', '#388bfd'];

  const chartConfig = {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{ data: data, backgroundColor: colors.slice(0, data.length), borderWidth: 2 }]
    },
    options: {
      legend: { position: 'right', labels: { fontSize: 11, boxWidth: 12 } },
      title: { display: true, text: 'Most Viewed Projects (Total: ' + total + ')', fontSize: 13 }
    }
  };

  return 'https://quickchart.io/chart?c=' + encodeURIComponent(JSON.stringify(chartConfig)) + '&w=520&h=230&bkg=white';
}

function sendEmailNotification(data, sheetUrl, stats) {
  const loc = [data.city, data.region, data.country].filter(Boolean).join(', ') || 'Unknown Location';
  const org = data.org && data.org !== 'Unknown' ? data.org : (data.isp || 'Unknown Network');
  const project = data.project_name || data.page_title || 'Engineering Portfolio';
  const page = data.page_path || 'index.html';
  const ref = data.referrer || 'Direct / Resume';
  const sessionDuration = data.session_duration || '< 1m';
  const timeStr = data.local_time || new Date().toLocaleString('en-US', { timeZone: 'America/Toronto', hour12: true });

  const subject = `Portfolio Website Visit: ${project} — ${loc} — ${timeStr}`;

  const chartUrl = generateVisitorChartUrl(stats.top, stats.total);
  const colors = ['#2ea043', '#58a6ff', '#a371f7', '#f0883e', '#d29922', '#388bfd'];

  let barTableHtml = '';
  if (stats.top && stats.top.length > 0) {
    barTableHtml = '<table style="width: 100%; font-size: 13px; border-collapse: collapse; margin-top: 8px;">';
    stats.top.forEach((p, idx) => {
      const col = colors[idx % colors.length];
      barTableHtml += `
        <tr style="border-bottom: 1px solid #f2f2f2;">
          <td style="padding: 6px 0; width: 44%; color: #222; font-weight: 500;">${p.name}</td>
          <td style="padding: 6px 8px; width: 38%;">
            <div style="background: #f0f0f0; border-radius: 4px; overflow: hidden; height: 12px; width: 100%;">
              <div style="background: ${col}; width: ${p.percent}%; height: 100%;"></div>
            </div>
          </td>
          <td style="padding: 6px 0; width: 18%; text-align: right; color: #555; font-family: monospace; font-size: 12px;">
            ${p.percent}% (${p.count})
          </td>
        </tr>`;
    });
    barTableHtml += '</table>';
  }

  const htmlBody = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 20px; border: 1px solid #e1e4e8; border-radius: 8px; background-color: #ffffff;">
      
      <div style="background-color: #0d1117; padding: 16px 20px; border-radius: 6px; margin-bottom: 20px;">
        <h2 style="color: #58a6ff; margin: 0; font-size: 17px; font-family: monospace;">
          ⚡ PORTFOLIO WEBSITE VISIT
        </h2>
        <div style="color: #8b949e; font-size: 12px; margin-top: 4px; font-family: monospace;">
          ${project} • ${timeStr} EDT
        </div>
      </div>

      <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 22px;">
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666; width: 150px;">📄 <strong>Project Viewed</strong></td>
          <td style="padding: 9px 0; color: #111;">
            <a href="${data.page_url}" style="color: #0969da; text-decoration: none; font-weight: bold; font-size: 15px;">${project}</a>
            <span style="color: #888; font-size: 12px; margin-left: 6px;">(${page})</span>
          </td>
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
          <td style="padding: 9px 0; color: #666;">⏳ <strong>Active Session Time</strong></td>
          <td style="padding: 9px 0; color: #111;">${sessionDuration}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">📱 <strong>Device</strong></td>
          <td style="padding: 9px 0; color: #111;">${data.device} (${data.screen_res})</td>
        </tr>
        <tr>
          <td style="padding: 9px 0; color: #666;">⏰ <strong>Time (EDT)</strong></td>
          <td style="padding: 9px 0; color: #111;">${data.local_time}</td>
        </tr>
      </table>

      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; margin-bottom: 22px;">
        <h3 style="margin: 0 0 12px 0; font-size: 15px; color: #1e293b; font-family: monospace;">
          📊 MOST VIEWED PROJECTS RANKING
        </h3>
        ${chartUrl ? `<div style="text-align: center; margin-bottom: 15px;"><img src="${chartUrl}" alt="Project Views Chart" style="max-width: 100%; height: auto; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);"></div>` : ''}
        ${barTableHtml}
      </div>

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
