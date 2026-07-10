/**
 * GLPI ↔ AppSheet Sync — Webhook (Google Apps Script)
 *
 * Deploy as a web app linked to the target spreadsheet.
 * All actions require a valid `token` parameter.
 *
 * Actions:
 *   getAll       — returns all rows as JSON
 *   getHeaders   — returns only the header row
 *   updateCell   — set a single cell value
 *   updateRow    — set all cells in one row (batch write)
 *   appendRow    — append a new row of values
 */

const AUTH_TOKEN = "glpi-sync-secret";

function doGet(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  const token = e.parameter && e.parameter.token;
  if (token !== AUTH_TOKEN) {
    return sendJson({status: "error", message: "Unauthorized"}, 403);
  }

  const action = e.parameter.action;
  const sheetName = e.parameter.sheet;
  if (!action || !sheetName) {
    return sendJson({status: "error", message: "Missing action or sheet"}, 400);
  }

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(sheetName);
    if (!sheet) {
      return sendJson({status: "error", message: `Sheet "${sheetName}" not found`}, 404);
    }

    switch (action) {
      case "getAll":
        return handleGetAll(sheet, e);
      case "getHeaders":
        return handleGetHeaders(sheet);
      case "updateCell":
        return handleUpdateCell(sheet, e);
      case "updateRow":
        return handleUpdateRow(sheet, e);
      case "appendRow":
        return handleAppendRow(sheet, e);
      default:
        return sendJson({status: "error", message: `Unknown action: ${action}`}, 400);
    }
  } catch (err) {
    return sendJson({status: "error", message: err.message}, 500);
  }
}

function handleGetAll(sheet, e) {
  const range = sheet.getDataRange();
  const values = range.getValues();
  if (values.length < 2) {
    return sendJson({status: "ok", headers: values[0] || [], data: []});
  }
  const headers = values[0];
  const data = [];
  for (let r = 1; r < values.length; r++) {
    const row = {};
    for (let c = 0; c < headers.length; c++) {
      row[headers[c]] = values[r][c];
    }
    data.push(row);
  }
  return sendJson({status: "ok", headers, data});
}

function handleGetHeaders(sheet) {
  const headers = sheet.getDataRange().getValues()[0] || [];
  return sendJson({status: "ok", headers});
}

function handleUpdateCell(sheet, e) {
  const row = parseInt(e.parameter.row, 10);
  const colName = e.parameter.col;
  const value = e.parameter.value;
  if (!row || !colName) {
    return sendJson({status: "error", message: "Missing row or col"}, 400);
  }
  const headers = sheet.getDataRange().getValues()[0];
  const colIndex = headers.indexOf(colName);
  if (colIndex === -1) {
    return sendJson({status: "error", message: `Column "${colName}" not found`}, 400);
  }
  sheet.getRange(row, colIndex + 1).setValue(value);
  return sendJson({status: "ok"});
}

function handleUpdateRow(sheet, e) {
  const row = parseInt(e.parameter.row, 10);
  const valuesParam = e.parameter.values;
  if (!row || !valuesParam) {
    return sendJson({status: "error", message: "Missing row or values"}, 400);
  }
  const values = JSON.parse(valuesParam);
  const headers = sheet.getDataRange().getValues()[0];
  const currentRow = sheet.getRange(row, 1, 1, headers.length).getValues()[0];
  for (let c = 0; c < headers.length; c++) {
    if (values.hasOwnProperty(headers[c])) {
      currentRow[c] = String(values[headers[c]]);
    }
  }
  sheet.getRange(row, 1, 1, headers.length).setValues([currentRow]);
  return sendJson({status: "ok"});
}

function handleAppendRow(sheet, e) {
  const valuesParam = e.parameter.values;
  if (!valuesParam) {
    return sendJson({status: "error", message: "Missing values"}, 400);
  }
  const data = JSON.parse(valuesParam);
  sheet.appendRow(data);
  return sendJson({status: "ok", row: sheet.getLastRow()});
}

function sendJson(data, statusCode) {
  const output = ContentService.createTextOutput(JSON.stringify(data));
  output.setMimeType(ContentService.MimeType.JSON);
  if (statusCode) {
    output.setStatusCode(statusCode);
  }
  return output;
}
