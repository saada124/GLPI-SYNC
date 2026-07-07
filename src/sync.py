from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from glpi_api import GLPIAPI
from sheets_client import SheetsClient
from field_mappings import EntityMapping
from cache import StateCache
from logger import setup_logger

logger = setup_logger()


class Syncer:
    def __init__(
        self,
        glpi: GLPIAPI,
        sheets: SheetsClient,
        mappings: dict[str, EntityMapping],
        cache: StateCache,
    ):
        self.glpi = glpi
        self.sheets = sheets
        self.mappings = mappings
        self.cache = cache
        self._errors: list[dict] = []

    def run(self) -> None:
        logger.info("=== Sync cycle started ===")
        self._errors.clear()

        for name, mapping in self.mappings.items():
            try:
                self._sync_direction_sheets_to_glpi(mapping)
            except Exception as e:
                logger.error(f"[{name}] Sheets→GLPI failed: {e}")
                self._errors.append({"entity": name, "direction": "sheets→glpi", "error": str(e)})

        for name, mapping in self.mappings.items():
            try:
                self._sync_direction_glpi_to_sheets(mapping)
            except Exception as e:
                logger.error(f"[{name}] GLPI→Sheets failed: {e}")
                self._errors.append({"entity": name, "direction": "glpi→sheets", "error": str(e)})

        now = datetime.now(timezone.utc).isoformat()
        self.cache.set_last_sync(now)

        if self._errors:
            logger.warning(f"Sync finished with {len(self._errors)} error(s)")
        else:
            logger.info("=== Sync cycle completed successfully ===")

        return self._errors

    def _hash_row(self, row: dict, mapping: EntityMapping) -> str:
        relevant = {
            k: row.get(k, "") for k in mapping.fields
            if k not in (mapping.glpi_id_col, mapping.synced_at_col, mapping.modified_at_col)
        }
        raw = json.dumps(relevant, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    def _sync_direction_sheets_to_glpi(self, mapping: EntityMapping) -> None:
        tab = mapping.sheet_tab
        logger.info(f"[{tab}] Checking Sheets→GLPI...")

        try:
            records = self.sheets.get_all_records(tab)
            headers = self.sheets.get_headers(tab)
        except Exception as e:
            logger.error(f"[{tab}] Failed to read sheet: {e}")
            return

        glpi_id_col = mapping.glpi_id_col
        synced_at_col = mapping.synced_at_col
        modified_at_col = mapping.modified_at_col

        for row_idx, row in enumerate(records, start=2):
            glpi_id = row.get(glpi_id_col) if glpi_id_col else None
            modified_at = row.get(modified_at_col) if modified_at_col else None
            synced_at = row.get(synced_at_col) if synced_at_col else ""

            if modified_at and synced_at:
                try:
                    if self._parse_timestamp(modified_at) <= self._parse_timestamp(synced_at):
                        continue
                except (ValueError, TypeError):
                    pass

            payload = mapping.sheet_to_glpi(row)
            if not payload:
                continue

            if not glpi_id or str(glpi_id).strip() == "":
                try:
                    new_id = self.glpi.add_item(mapping.api_endpoint, payload)
                    if glpi_id_col:
                        self.sheets.update_cell(tab, row_idx, glpi_id_col, str(new_id))
                    logger.info(f"[{tab}] Created GLPI ID {new_id}")
                except Exception as e:
                    logger.error(f"[{tab}] Failed to create row {row_idx}: {e}")
                    self._errors.append({"entity": tab, "row": row_idx, "error": str(e)})
                    continue
            else:
                try:
                    self.glpi.update_item(mapping.api_endpoint, int(glpi_id), payload)
                    logger.info(f"[{tab}] Updated GLPI ID {glpi_id}")
                except Exception as e:
                    logger.error(f"[{tab}] Failed to update GLPI ID {glpi_id}: {e}")
                    self._errors.append({"entity": tab, "glpi_id": glpi_id, "error": str(e)})
                    continue

            if synced_at_col:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                self.sheets.update_cell(tab, row_idx, synced_at_col, now)

    def _sync_direction_glpi_to_sheets(self, mapping: EntityMapping) -> None:
        tab = mapping.sheet_tab
        logger.info(f"[{tab}] Checking GLPI→Sheets...")

        last_sync = self.cache.get_last_sync()
        criteria = [{"field": "date_mod", "searchtype": "greater", "value": last_sync}]

        try:
            glpi_records = self.glpi.search(mapping.api_endpoint, criteria)
        except Exception as e:
            logger.error(f"[{tab}] GLPI search failed: {e}")
            return

        if not glpi_records:
            logger.info(f"[{tab}] No GLPI changes since {last_sync}")
            return

        try:
            headers = self.sheets.get_headers(tab)
            records = self.sheets.get_all_records(tab)
        except Exception as e:
            logger.error(f"[{tab}] Failed to read sheet: {e}")
            return

        glpi_id_col = mapping.glpi_id_col
        synced_at_col = mapping.synced_at_col
        tolerance_s = 2

        for glpi_row in glpi_records:
            glpi_id = glpi_row.get("id")
            if not glpi_id:
                continue

            date_mod = glpi_row.get("date_mod", "")

            existing_row_idx = None
            for idx, sheet_row in enumerate(records, start=2):
                if str(sheet_row.get(glpi_id_col, "")).strip() == str(glpi_id):
                    existing_row_idx = idx
                    break

            if existing_row_idx and synced_at_col:
                existing_synced = records[existing_row_idx - 2].get(synced_at_col, "")
                if existing_synced and date_mod:
                    try:
                        glpi_ts = self._parse_timestamp(date_mod)
                        sheet_ts = self._parse_timestamp(existing_synced)
                        if abs((glpi_ts - sheet_ts).total_seconds()) <= tolerance_s:
                            continue
                    except (ValueError, TypeError):
                        pass

            row_data: dict[str, Any] = {}
            for sheet_col, glpi_field in mapping.fields.items():
                if not glpi_field:
                    continue
                glpi_val = glpi_row.get(glpi_field, "")
                if sheet_col in mapping.code_lookups:
                    reverse = {v: k for k, v in mapping.code_lookups[sheet_col].items()}
                    row_data[sheet_col] = reverse.get(glpi_val, glpi_val)
                else:
                    row_data[sheet_col] = glpi_val

            if existing_row_idx:
                self.sheets.update_row(tab, existing_row_idx, row_data)
                if synced_at_col:
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    self.sheets.update_cell(tab, existing_row_idx, synced_at_col, now)
                logger.info(f"[{tab}] Updated sheet row for GLPI ID {glpi_id}")
            else:
                row_data[glpi_id_col] = str(glpi_id) if glpi_id_col else ""
                self.sheets.append_row(tab, row_data, headers)
                logger.info(f"[{tab}] Appended new sheet row for GLPI ID {glpi_id}")

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(ts.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        raise ValueError(f"Cannot parse timestamp: {ts}")


import json  # noqa: E402 (needed by _hash_row)
