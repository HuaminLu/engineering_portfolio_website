/**
 * Google Apps Script for Huamin Lu's Engineering Portfolio Analytics
 * 
 * INSTRUCTIONS:
 * 1. Go to https://script.google.com and click "New project".
 * 2. Delete any code in Code.gs and paste this ENTIRE file into it.
 * 3. Click "Deploy" -> "New deployment".
 * 4. Select type: "Web app".
 * 5. Set:
 *    - Description: Portfolio Analytics
 *    - Execute as: "Me (luhuaminlu@gmail.com)"
 *    - Who has access: "Anyone"   <-- CRITICAL: Must be "Anyone" so visitor browsers can log hits!
 * 6. Click "Deploy", authorize permissions when prompted.
 * 7. Copy the "Web app URL" (starts with https://script.google.com/macros/s/...)
 * 8. Paste that URL into js/tracker.js at line 14:
 *    const GOOGLE_SCRIPT_URL = 'YOUR_COPIED_URL';
 */

// Configuration
const RECIPIENT_EMAIL = 'luhuaminlu@gmail.com';
const SPREADSHEET_NAME = 'Portfolio Visitor Analytics';

// Set to true if you want an email for EVERY page view.
// Set to false (recommended) for 1 email per visitor session to prevent inbox clutter.
const SEND_EMAIL_ON_EVERY_PAGE = false;

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return ContentService.createTextOutput(JSON.stringify({ status: "empty" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const data = JSON.parse(e.postData.contents);
    const ss = getOrCreateSpreadsheet();
    const sheet = getOrCreateSheet(ss);

    // Append visit record to Google Sheet
    sheet.appendRow([
      data.local_time || new Date().toISOString(),
      data.city || 'Unknown',
      data.region || 'Unknown',
      data.country || 'Unknown',
      data.org || data.isp || 'Unknown',
      data.page_title || 'Engineering Portfolio',
      data.page_path || 'index.html',
      data.referrer || 'Direct / Bookmark',
      data.device || 'Unknown',
      data.screen_res || 'Unknown',
      data.ip || 'Hidden',
      data.session_id || 'Unknown'
    ]);

    // Determine whether to send email notification
    const shouldSendEmail = SEND_EMAIL_ON_EVERY_PAGE || data.is_new_session;

    if (shouldSendEmail) {
      sendEmailNotification(data, ss.getUrl());
    }

    return ContentService.createTextOutput(JSON.stringify({ status: "success" }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function sendEmailNotification(data, sheetUrl) {
  const loc = [data.city, data.region, data.country].filter(Boolean).join(', ') || 'Unknown Location';
  const org = data.org && data.org !== 'Unknown' ? data.org : (data.isp || 'Unknown Network');
  const page = data.page_path || 'index.html';
  const ref = data.referrer || 'Direct';

  const subject = `🚀 Portfolio Visit: ${loc} — ${page} (${ref})`;

  const htmlBody = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px; background-color: #ffffff;">
      <div style="background-color: #0d1117; padding: 16px 20px; border-radius: 6px; margin-bottom: 20px;">
        <h2 style="color: #58a6ff; margin: 0; font-size: 18px; font-family: monospace;">⚡ PORTFOLIO VISITOR DETECTED</h2>
      </div>

      <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 20px;">
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px 0; color: #666; width: 140px;">📍 <strong>Location</strong></td>
          <td style="padding: 10px 0; color: #111;"><strong>${loc}</strong></td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px 0; color: #666;">🏢 <strong>Network / ISP</strong></td>
          <td style="padding: 10px 0; color: #111;">${org}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px 0; color: #666;">📄 <strong>Page Viewed</strong></td>
          <td style="padding: 10px 0; color: #111;"><a href="${data.page_url}" style="color: #0969da; text-decoration: none;">${data.page_title} (${page})</a></td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px 0; color: #666;">🔗 <strong>Referrer</strong></td>
          <td style="padding: 10px 0; color: #111;"><strong>${ref}</strong></td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px 0; color: #666;">📱 <strong>Device</strong></td>
          <td style="padding: 10px 0; color: #111;">${data.device} (${data.screen_res})</td>
        </tr>
        <tr>
          <td style="padding: 10px 0; color: #666;">⏰ <strong>Time (EDT)</strong></td>
          <td style="padding: 10px 0; color: #111;">${data.local_time}</td>
        </tr>
      </table>

      <div style="text-align: center; margin-top: 25px;">
        <a href="${sheetUrl}" style="background-color: #238636; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">📊 View Full Visitor Log in Google Sheets</a>
      </div>
      
      <p style="color: #888; font-size: 11px; margin-top: 25px; text-align: center;">
        Your own visits from registered admin devices are automatically excluded.
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
    // Remove default Sheet1 if present
    const defaultSheet = ss.getSheetByName('Sheet1');
    if (defaultSheet && ss.getSheets().length > 1) {
      ss.deleteSheet(defaultSheet);
    }
    
    // Set headers
    const headers = [
      'Timestamp (EDT)', 'City', 'Region / State', 'Country', 
      'Network / ISP', 'Page Title', 'Page Path', 'Referrer', 
      'Device', 'Screen Res', 'IP', 'Session ID'
    ];
    sheet.appendRow(headers);

    // Style headers
    const headerRange = sheet.getRange(1, 1, 1, headers.length);
    headerRange.setBackground('#0d1117');
    headerRange.setFontColor('#ffffff');
    headerRange.setFontWeight('bold');
    headerRange.setFontFamily('Roboto');
    sheet.setFrozenRows(1);
    sheet.setRowHeight(1, 32);
  }
  return sheet;
}
