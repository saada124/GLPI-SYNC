// Google Apps Script — acts as a bridge between Python and the Sheet
const TOKEN = "my-secret-sync-token-123";  // ← change this

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  const headers = { "Content-Type": "application/json" };

  // Simple auth check
  if (!e.parameter.token || e.parameter.token !== TOKEN) {
    return ContentService.createTextOutput(JSON.stringify({ error: "Unauthorized" }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  try {
    const action = e.parameter.action;
    const sheetName = e.parameter.sheet || "Sheet1";
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const ws = ss.getSheetByName(sheetName);
    if (!ws) return jsonResponse({ error: "Sheet not found" });

    if (action === "getAll") {
      const data = ws.getDataRange().getValues();
      const headers = data[0];
      const rows = data.slice(1).map(r => {
        const obj = {};
        headers.forEach((h, i) => obj[h] = r[i]);
        return obj;
      });
      return jsonResponse({ data: rows, headers });

    } else if (action === "updateCell") {
      const row = parseInt(e.parameter.row);
      const col = e.parameter.col;
      const value = e.parameter.value;
      const colIdx = ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0].indexOf(col) + 1;
      if (colIdx === 0) return jsonResponse({ error: "Column not found" });
      ws.getRange(row, colIdx).setValue(value);
      return jsonResponse({ success: true });

    } else if (action === "appendRow") {
      const values = JSON.parse(e.parameter.values);
      ws.appendRow(values);
      return jsonResponse({ success: true });

    } else {
      return jsonResponse({ error: "Unknown action" });
    }
  } catch (err) {
    return jsonResponse({ error: err.toString() });
  }
}

function jsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}