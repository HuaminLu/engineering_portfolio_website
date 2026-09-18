/**
 * Google Apps Script for Huamin Lu's Engineering Portfolio Analytics
 * 
 * FEATURES:
 * - Real-time Gmail alerts on visitor pageviews.
 * - Exact project-level tracking (e.g. Unitree G1, 7-DOF Arm, 10-DOF Hand).
 * - Tracks dwell time (duration on each project page & total session duration).
 * - Generates an interactive Pie / Doughnut Chart and HTML Bar Graph ranking
 *   the most viewed projects directly inside every email alert.
 * - Auto-creates and logs every visit to Google Sheet "Portfolio Visitor Analytics".
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

// Set to true for an email alert on every project page view.
// Set to false for 1 email alert per visitor session.
const SEND_EMAIL_ON_EVERY_PAGE = true;

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return ContentService.createTextOutput(JSON.stringify({ status: "empty" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const data = JSON.parse(e.postData.contents);
    const ss = getOrCreateSpreadsheet();
    const sheet = getOrCreateSheet(ss);

    // If this is an exit beacon updating duration for the last page
    if (data.type === 'exit' && data.session_id) {
      updateLastRowDuration(sheet, data.session_id, data.duration_str);
      return ContentService.createTextOutput(JSON.stringify({ status: "exit_updated" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // Append visit record to Google Sheet
    sheet.appendRow([
      data.local_time || new Date().toISOString(),
      data.city || 'Unknown',
      data.region || 'Unknown',
      data.country || 'Unknown',
      data.org || data.isp || 'Unknown',
      data.project_name || data.page_title || 'Engineering Portfolio',
      data.page_path || 'index.html',
      data.prev_page_info || 'First Page of Session',
      data.session_duration || '< 1m',
      data.referrer || 'Direct / Bookmark',
      data.device || 'Unknown',
      data.screen_res || 'Unknown',
      data.ip || 'Hidden',
      data.session_id || 'Unknown'
    ]);

    // Query stats across the sheet for project rankings and chart
    const stats = getProjectStats(sheet);

    // Determine whether to send email
    const shouldSendEmail = SEND_EMAIL_ON_EVERY_PAGE || data.is_new_session;
    if (shouldSendEmail) {
      sendEmailNotification(data, ss.getUrl(), stats);
    }

    return ContentService.createTextOutput(JSON.stringify({ status: "success" }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function updateLastRowDuration(sheet, sessionId, durationStr) {
  try {
    const lastRow = sheet.getLastRow();
    if (lastRow > 1) {
      // Check last few rows for matching session_id in column 14
      for (let r = lastRow; r >= Math.max(2, lastRow - 5); r--) {
        const rowSession = sheet.getRange(r, 14).getValue();
        if (rowSession === sessionId) {
          sheet.getRange(r, 8).setValue('Time on page: ' + durationStr);
          break;
        }
      }
    }
  } catch (e) {}
}

function getProjectStats(sheet) {
  const data = sheet.getDataRange().getValues();
  const counts = {};
  let totalProjectViews = 0;

  for (let i = 1; i < data.length; i++) {
    const project = data[i][5]; // Column F: Project / Page Title
    if (project && typeof project === 'string' && !project.startsWith('[TEST]')) {
      counts[project] = (counts[project] || 0) + 1;
      totalProjectViews++;
    }
  }

  const sorted = Object.keys(counts).map(name => ({
    name: name,
    count: counts[name],
    percent: totalProjectViews > 0 ? Math.round((counts[name] / totalProjectViews) * 100) : 0
  })).sort((a, b) => b.count - a.count);

  return {
    top: sorted.slice(0, 6),
    total: totalProjectViews
  };
}

function generateQuickChartUrl(topProjects, total) {
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
      datasets: [{
        data: data,
        backgroundColor: colors.slice(0, data.length),
        borderWidth: 2
      }]
    },
    options: {
      legend: { position: 'right', labels: { fontSize: 11, boxWidth: 12 } },
      title: { display: true, text: 'Most Viewed Projects (Total Views: ' + total + ')', fontSize: 13 }
    }
  };

  return 'https://quickchart.io/chart?c=' + encodeURIComponent(JSON.stringify(chartConfig)) + '&w=520&h=230&bkg=white';
}

function sendEmailNotification(data, sheetUrl, stats) {
  const loc = [data.city, data.region, data.country].filter(Boolean).join(', ') || 'Unknown Location';
  const org = data.org && data.org !== 'Unknown' ? data.org : (data.isp || 'Unknown Network');
  const project = data.project_name || data.page_title || 'Engineering Portfolio';
  const page = data.page_path || 'index.html';
  const ref = data.referrer || 'Direct';
  const timeSpent = data.prev_page_info ? data.prev_page_info : 'Session Start';
  const sessionDuration = data.session_duration || '< 1m';

  const isTest = data.page_title && data.page_title.startsWith('[TEST]');
  const prefix = isTest ? '🧪 [TEST] ' : '🚀 ';
  const subject = `${prefix}Portfolio View: ${project} — ${loc} (${ref})`;

  // Generate chart URL
  const chartUrl = generateQuickChartUrl(stats.top, stats.total);

  // Build HTML Bar Graph
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
          <td style="padding: 6px 0; width: 18%; text-align: right; color: #555; font-family: monospace; font-size: 12px;">${p.percent}% (${p.count})</td>
        </tr>`;
    });
    barTableHtml += '</table>';
  }

  const htmlBody = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 20px; border: 1px solid #e1e4e8; border-radius: 8px; background-color: #ffffff;">
      
      <!-- Top Banner -->
      <div style="background-color: #0d1117; padding: 16px 20px; border-radius: 6px; margin-bottom: 20px;">
        <h2 style="color: #58a6ff; margin: 0; font-size: 17px; font-family: monospace;">
          ⚡ ${isTest ? 'TEST VISIT RECORDED' : 'RECRUITER / VISITOR DETECTED'}
        </h2>
      </div>

      <!-- Visit Details Table -->
      <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 22px;">
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666; width: 150px;">📄 <strong>Project Viewed</strong></td>
          <td style="padding: 9px 0; color: #111;">
            <a href="${data.page_url}" style="color: #0969da; text-decoration: none; font-weight: bold; font-size: 15px;">
              ${project}
            </a>
            <span style="color: #888; font-size: 12px; margin-left: 6px;">(${page})</span>
          </td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">⏱️ <strong>Previous Page Dwell</strong></td>
          <td style="padding: 9px 0; color: #111;"><strong>${timeSpent}</strong></td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 9px 0; color: #666;">⏳ <strong>Total Session Time</strong></td>
          <td style="padding: 9px 0; color: #111;">${sessionDuration}</td>
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
          <td style="padding: 9px 0; color: #666;">⏰ <strong>Time (EDT)</strong></td>
          <td style="padding: 9px 0; color: #111;">${data.local_time}</td>
        </tr>
      </table>

      <!-- Analytics Chart Section -->
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; margin-bottom: 22px;">
        <h3 style="margin: 0 0 12px 0; font-size: 15px; color: #1e293b; font-family: monospace;">
          📊 MOST VIEWED PROJECTS RANKING
        </h3>
        
        <!-- Doughnut / Pie Chart Image -->
        ${chartUrl ? `<div style="text-align: center; margin-bottom: 15px;"><img src="${chartUrl}" alt="Project Views Chart" style="max-width: 100%; height: auto; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);"></div>` : ''}

        <!-- Bar Breakdown -->
        ${barTableHtml}
      </div>

      <!-- Action Button -->
      <div style="text-align: center; margin: 25px 0 10px 0;">
        <a href="${sheetUrl}" style="background-color: #238636; color: #ffffff; padding: 11px 22px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 14px;">
          📊 Open Visitor Log in Google Sheets
        </a>
      </div>
      
      <p style="color: #888; font-size: 11px; margin-top: 20px; text-align: center;">
        Your registered admin devices are automatically excluded from logs and alerts.
      </p>
    </div>
  `;

  MailApp.sendEmail({
    to: RECIPIENT_EMAIL,
    subject: subject,
    htmlBody: htmlBody
  });
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
      'Network / ISP', 'Project / Page Title', 'Page Path', 'Prev Page Dwell',
      'Session Duration', 'Referrer', 'Device', 'Screen Res', 'IP', 'Session ID'
    ];
    sheet.appendRow(headers);
    const headerRange = sheet.getRange(1, 1, 1, headers.length);
    headerRange.setBackground('#0d1117');
    headerRange.setFontColor('#ffffff');
    headerRange.setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.setRowHeight(1, 32);
  } else {
    // Ensure header row has modern columns if existing sheet was created earlier
    const headerVal = sheet.getRange(1, 8).getValue();
    if (headerVal === 'Referrer') {
      sheet.insertColumnsAfter(7, 2);
      sheet.getRange(1, 8).setValue('Prev Page Dwell');
      sheet.getRange(1, 9).setValue('Session Duration');
    }
  }
  return sheet;
}
