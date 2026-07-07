from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


class StateCache:
    def __init__(self):
        CACHE_DIR.mkdir(exist_ok=True)
        self._file = CACHE_DIR / "state.json"
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self._file.write_text(
            json.dumps(self._data, indent=2, default=str),
            encoding="utf-8",
        )

    def get_last_sync(self) -> str:
        return self._data.get("last_sync", "1970-01-01T00:00:00")

    def set_last_sync(self, timestamp: str) -> None:
        self._data["last_sync"] = timestamp
        self._save()

    def get_row_hash(self, tab: str, glpi_id: int) -> str | None:
        return self._data.get("row_hashes", {}).get(tab, {}).get(str(glpi_id))

    def set_row_hash(self, tab: str, glpi_id: int, hash_val: str) -> None:
        self._data.setdefault("row_hashes", {})
        self._data["row_hashes"].setdefault(tab, {})
        self._data["row_hashes"][tab][str(glpi_id)] = hash_val
        self._save()

    def get_all_glpi_ids(self, tab: str) -> dict[str, str]:
        return self._data.get("row_hashes", {}).get(tab, {}).copy()
