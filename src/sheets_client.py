from __future__ import annotations

import json
from typing import Any
import requests


class SheetsClient:
    def __init__(self, webhook_url: str, token: str):
        self.url = webhook_url
        self.token = token

    def _call(self, action: str, sheet: str, **params) -> dict:
        params["action"] = action
        params["sheet"] = sheet
        params["token"] = self.token
        resp = requests.get(self.url, params=params, timeout=120)
        resp.raise_for_status()
        try:
            return resp.json()
        except requests.exceptions.JSONDecodeError as e:
            raise RuntimeError(f"Webhook returned non-JSON for {action} on {sheet}: {resp.text[:500]}") from e

    def get_all_records(self, tab: str) -> list[dict[str, Any]]:
        result = self._call("getAll", sheet=tab)
        return result.get("data", [])

    def update_cell(self, tab: str, row: int, col_name: str, value: Any) -> None:
        self._call("updateCell", sheet=tab, row=str(row), col=col_name, value=str(value))

    def batch_update_rows(self, tab: str, updates: list[tuple[int, dict[str, Any]]]) -> None:
        rows_data = []
        for row_num, data in updates:
            filtered = {k: v for k, v in data.items() if v != "" and v is not None and v != 0}
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
        filtered = {k: v for k, v in data.items() if v != "" and v is not None and v != 0}
        if not filtered:
            return
        self._call("updateRow", sheet=tab, row=str(row), values=json.dumps(filtered))
