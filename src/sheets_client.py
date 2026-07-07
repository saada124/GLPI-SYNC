from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials
from typing import Any


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsClient:
    def __init__(self, creds_path: str, sheet_id: str):
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        self.client = gspread.authorize(creds)
        self.sheet_id = sheet_id
        self._spreadsheet = self.client.open_by_key(sheet_id)

    @property
    def spreadsheet(self):
        return self._spreadsheet

    def worksheet(self, name: str) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(name)

    def get_all_records(self, tab: str) -> list[dict[str, Any]]:
        ws = self.worksheet(tab)
        return ws.get_all_records()

    def get_headers(self, tab: str) -> list[str]:
        ws = self.worksheet(tab)
        return ws.row_values(1)

    def find_row_by_value(self, tab: str, col_name: str, value: Any) -> int | None:
        ws = self.worksheet(tab)
        headers = ws.row_values(1)
        if col_name not in headers:
            return None
        col_idx = headers.index(col_name) + 1
        cell = ws.find(str(value), in_column=col_idx)
        return cell.row if cell else None

    def update_cell(self, tab: str, row: int, col_name: str, value: Any) -> None:
        ws = self.worksheet(tab)
        headers = ws.row_values(1)
        if col_name not in headers:
            return
        col_idx = headers.index(col_name) + 1
        ws.update_cell(row, col_idx, value)

    def update_row(self, tab: str, row: int, data: dict[str, Any]) -> None:
        ws = self.worksheet(tab)
        headers = ws.row_values(1)
        for col_name, value in data.items():
            if col_name in headers:
                col_idx = headers.index(col_name) + 1
                ws.update_cell(row, col_idx, value)

    def append_row(self, tab: str, data: dict[str, Any], headers: list[str]) -> None:
        ws = self.worksheet(tab)
        values = [str(data.get(h, "")) for h in headers]
        ws.append_row(values)

    def get_worksheet_names(self) -> list[str]:
        return [ws.title for ws in self._spreadsheet.worksheets()]
