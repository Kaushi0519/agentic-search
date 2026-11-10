/**
 * Google Apps Script attached to the application tracker sheet.
 * The auto-apply bot POSTs each submitted application here and it becomes a row.
 *
 * Setup: Extensions → Apps Script, paste this file, set the SECRET script
 * property, then Deploy → New deployment → Web app (execute as me, anyone).
 */

const TAB_GID = 1732729536; // the tracker tab (gid in the sheet's URL)

function tab_() {
  const sheet = SpreadsheetApp.getActive().getSheets().find((s) => s.getSheetId() === TAB_GID);
  if (!sheet) throw new Error(`No tab with gid ${TAB_GID}`);
  return sheet;
}

function headers_(sheet) {
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map((h) => String(h).trim());
}

function reply_(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}

function authorized_(secret) {
  return secret && secret === PropertiesService.getScriptProperties().getProperty("SECRET");
}

/** GET ?secret=…  →  the header row, so the bot can check the columns. */
function doGet(e) {
  if (!authorized_(e.parameter.secret)) return reply_({ error: "unauthorized" });
  return reply_({ headers: headers_(tab_()) });
}

/**
 * POST {"secret": "…", "row": {"Company": "…", "Role": "…", "Application Date": "2026-11-09", …}}
 * Keys are header names; columns not mentioned stay blank.
 */
function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (!authorized_(body.secret)) return reply_({ error: "unauthorized" });

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = tab_();
    const headers = headers_(sheet);
    const unknown = Object.keys(body.row).filter((k) => !headers.includes(k));
    if (unknown.length) return reply_({ error: "unknown columns", unknown, headers });

    const values = headers.map((h) => {
      const v = body.row[h];
      if (v == null) return "";
      // Dates as real dates so they format like the rest of the column.
      return /date/i.test(h) && /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T12:00:00`) : v;
    });

    // Next row after the last filled Company cell (dropdown columns run further down).
    const companies = sheet.getRange(1, 1, sheet.getMaxRows(), 1).getValues();
    let row = companies.length;
    while (row > 1 && companies[row - 1][0] === "") row--;
    row += 1;
    if (row > sheet.getMaxRows()) sheet.insertRowAfter(sheet.getMaxRows());
    sheet.getRange(row, 1, 1, values.length).setValues([values]);
    return reply_({ ok: true, row });
  } finally {
    lock.releaseLock();
  }
}
