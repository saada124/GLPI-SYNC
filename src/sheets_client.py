from __future__ import annotations

import json
from typing import Any
import requests


class SheetsClient:
    def __init__(self, webhook_url: str, token: str):
        self.url = webhook_url
        self.token = token
        self._session = requests.Session()

    def _call(self, action: str, sheet: str, **params) -> dict:
        params["action"] = action
        params["sheet"] = sheet
        params["token"] = self.token
        resp = self._session.get(self.url, params=params, timeout=120)
        resp.raise_for_status()
        try:
            return resp.json()
        except requests.exceptions.JSONDecodeError as e:
            raise RuntimeError(
                f"Webhook returned non-JSON for {action} on {sheet}: {resp.text[:500]}"
            ) from e

    def get_all_records(self, tab: str, page_size: int = 2000) -> list[dict[str, Any]]:
        """Fetch every row in `tab`, paginating server-side calls so a large
        sheet never forces a single giant response (or risks Apps Script's
        6-minute execution limit on one request).
        """
        all_rows: list[dict[str, Any]] = []
        start = 0
        while True:
            result = self._call("getAll", sheet=tab, start=str(start), limit=str(page_size))
            page = result.get("data", [])
            all_rows.extend(page)
            total = result.get("total")
            start += len(page)
            if not page or len(page) < page_size:
                break
            if total is not None and start >= total:
                break
        return all_rows

    def update_cell(self, tab: str, row: int, col_name: str, value: Any) -> None:
        self._call("updateCell", sheet=tab, row=str(row), col=col_name, value=str(value))

    @staticmethod
    def _filter_values(data: dict[str, Any]) -> dict[str, Any]:
        # Keep 0 / False — status codes and flags are valid.
        return {k: v for k, v in data.items() if v is not None and v != ""}

    def batch_update_rows(self, tab: str, updates: list[tuple[int, dict[str, Any]]]) -> None:
        rows_data = []
        for row_num, data in updates:
            filtered = self._filter_values(data)
            if filtered:
                rows_data.append({"row": row_num, "values": filtered})
        if not rows_data:
            return
        try:
            self._call("batchUpdateRows", sheet=tab, rows=json.dumps(rows_data))
        except Exception:
            for row_num, data in updates:
                self.update_row(tab, row_num, data)

    def update_row(self, tab: str, row: int, data: dict[str, Any]) -> None:
        filtered = self._filter_values(data)
        if not filtered:
            return
        self._call("updateRow", sheet=tab, row=str(row), values=json.dumps(filtered))