/**
 * ILINA policy radar: shortlist, folders and watch list, kept in a "Watchlist" tab of this Google Sheet.
 *
 * The dashboard saves to it when you shortlist or watch something, the agent reads it every morning, and the
 * agent's emails go out through it (from this sheet owner's Gmail, to TO only).
 *
 * Setup (once): Extensions → Apps Script, paste this file, fill in KEY and TO, then
 * Deploy → New deployment → Web app, Execute as: Me, Who has access: Anyone. Copy the web app URL.
 * You can add your own columns (e.g. notes) to the Watchlist tab; just keep the existing column names.
 */
const KEY = 'PASTE-THE-WATCH_KEY-HERE';   // same value as the WATCH_KEY GitHub secret
const TO = 'PASTE-HER-EMAIL-HERE';        // the only address this script will ever mail (comma-separate several)
const TAB = 'Watchlist';
const COLS = ['title', 'folder', 'watch', 'keywords', 'mark', 'added', 'watch_since', 'link', 'id'];

function tab_() {
  const ss = SpreadsheetApp.getActive();
  let sh = ss.getSheetByName(TAB);
  if (!sh) {
    sh = ss.insertSheet(TAB);
    sh.appendRow(COLS);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, COLS.length).setFontWeight('bold');
    sh.setColumnWidth(1, 380);
    sh.getRange('C2:C').insertCheckboxes();
    sh.getRange('D1').setNote('Optional. Comma-separated words or phrases: you get an alert when new items mention any of them.');
  }
  return sh;
}

const day_ = d => Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
const json_ = o => ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);

function read_() {
  const v = tab_().getDataRange().getValues();
  const head = v.shift();
  return v.filter(r => r[head.indexOf('id')]).map(r =>
    Object.fromEntries(head.map((k, i) => [k, r[i] instanceof Date ? day_(r[i]) : r[i]])));
}

function doPost(e) {
  const b = JSON.parse(e.postData.contents);
  if (b.key !== KEY) return json_({error: 'wrong key'});
  if (b.action === 'list') return json_({rows: read_()});
  if (b.action === 'mail') return mail_(b);
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sh = tab_();
    const v = sh.getDataRange().getValues();
    const head = v[0], at = head.indexOf('id');
    const n = v.findIndex((r, i) => i > 0 && r[at] === b.id);
    if (!b.mark && !b.watch) {            // un-shortlisted and un-watched: remove the row
      if (n > 0) sh.deleteRow(n + 1);
      return json_({ok: true});
    }
    const prev = n > 0 ? Object.fromEntries(head.map((k, i) => [k, v[n][i]])) : {};
    const today = day_(new Date());
    const next = {
      id: b.id, title: b.title, link: b.link, mark: b.mark || '', folder: b.folder || '', watch: !!b.watch,
      keywords: prev.keywords || '',      // her own edits in the sheet are never overwritten
      added: prev.added || today,
      watch_since: b.watch ? (prev.watch === true && prev.watch_since ? prev.watch_since : today) : '',
    };
    const row = head.map((k, i) => k in next ? next[k] : (n > 0 ? v[n][i] : ''));
    if (n > 0) sh.getRange(n + 1, 1, 1, row.length).setValues([row]);
    else sh.appendRow(row);
    return json_({ok: true});
  } finally {
    lock.releaseLock();
  }
}

function mail_(b) {
  const opts = {htmlBody: b.html, name: 'ILINA policy radar'};
  if (b.attachment) {
    opts.attachments = [Utilities.newBlob(Utilities.base64Decode(b.attachment.b64),
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', b.attachment.name)];
  }
  MailApp.sendEmail(TO, b.subject, 'This email needs HTML to display.', opts);
  return json_({ok: true});
}
