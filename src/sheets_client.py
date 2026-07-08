from __future__ import annotations

import json
from typing import Any
import requests


class SheetsClient:
    def __init__(self, webhook_url: str, token: str):
        self.url = webhook_url
        self.token = token
        self._headers_cache: dict[str, list[str]] = {}

    def _call(self, action: str, sheet: str, **params) -> dict:
        params["action"] = action
        params["sheet"] = sheet
        params["token"] = self.token
        resp = requests.get(self.url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_all_records(self, tab: str) -> list[dict[str, Any]]:
        result = self._call("getAll", sheet=tab)
        self._headers_cache[tab] = result.get("headers", [])
        return result.get("data", [])

    def get_headers(self, tab: str) -> list[str]:
        if tab not in self._headers_cache:
            self.get_all_records(tab)
        return self._headers_cache.get(tab, [])

    def update_cell(self, tab: str, row: int, col_name: str, value: Any) -> None:
        self._call("updateCell", sheet=tab, row=str(row), col=col_name, value=str(value))

    def update_row(self, tab: str, row: int, data: dict[str, Any]) -> None:
        for col_name, value in data.items():
            self.update_cell(tab, row, col_name, value)

    def append_row(self, tab: str, data: dict[str, Any], headers: list[str]) -> None:
        values = json.dumps([str(data.get(h, "")) for h in headers])
        self._call("appendRow", sheet=tab, values=values)
