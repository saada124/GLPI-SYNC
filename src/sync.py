from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from glpi_api import GLPIAPI
from sheets_client import SheetsClient
from field_mappings import EntityMapping
from cache import StateCache
from lookup import LookupCache
from logger import setup_logger

logger = setup_logger()


class Syncer:
    def __init__(
        self,
        glpi: GLPIAPI,
        sheets: SheetsClient,
        mappings: dict[str, EntityMapping],
        cache: StateCache,
        lookups: LookupCache,
    ):
        self.glpi = glpi
        self.sheets = sheets
        self.mappings = mappings
        self.cache = cache
        self.lookups = lookups
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

            # Apply text→ID lookups for reference columns
            for col_name, ref_type in mapping.lookups.items():
                raw_value = row.get(col_name)
                if raw_value:
                    glpi_field = mapping.fields.get(col_name)
                    resolved = self.lookups.resolve(ref_type, str(raw_value).strip())
                    if resolved is not None and glpi_field:
                        payload[glpi_field] = resolved
                    elif glpi_field and glpi_field in payload:
                        del payload[glpi_field]

            # Special handling: ticket_assignments resolves IDs via GLPI_ID columns
            if tab == "ticket_assignments":
                payload = self._resolve_ticket_assignment_ids(row, payload)
                if payload is None:
                    continue

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

    def _resolve_ticket_assignment_ids(
        self, row: dict, payload: dict
    ) -> dict | None:
        ticket_appsheet_id = str(row.get("Ticket_ID", "")).strip()
        user_appsheet_id = str(row.get("User_ID", "")).strip()

        # Look up GLPI_ID from Tickets tab
        try:
            tickets_records = self.sheets.get_all_records("Tickets")
        except Exception:
            logger.error("[ticket_assignments] Could not read Tickets sheet")
            return None

        glpi_ticket_id = None
        for trec in tickets_records:
            if str(trec.get("Ticket_ID", "")).strip() == ticket_appsheet_id:
                gid = trec.get("GLPI_ID", "")
                if gid:
                    glpi_ticket_id = int(gid)
                    break

        glpi_user_id = None
        if user_appsheet_id:
            try:
                users_records = self.sheets.get_all_records("Users")
            except Exception:
                users_records = []
            for urec in users_records:
                if str(urec.get("User_ID", "")).strip() == user_appsheet_id:
                    gid = urec.get("GLPI_ID", "")
                    if gid:
                        glpi_user_id = int(gid)
                    break

        if not glpi_ticket_id:
            logger.warning(
                f"[ticket_assignments] No GLPI ticket ID found for Ticket_ID={ticket_appsheet_id}"
            )
            self._errors.append({
                "entity": "ticket_assignments",
                "ticket_id": ticket_appsheet_id,
                "error": "Ticket not yet synced to GLPI or GLPI_ID missing",
            })
            return None

        payload["tickets_id"] = glpi_ticket_id
        if glpi_user_id:
            payload["users_id"] = glpi_user_id

        return payload

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

                # Reverse code lookup
                if sheet_col in mapping.code_lookups:
                    rev = {v: k for k, v in mapping.code_lookups[sheet_col].items()}
                    row_data[sheet_col] = rev.get(glpi_val, glpi_val)
                # Reverse reference lookup
                elif sheet_col in mapping.lookups:
                    ref_type = mapping.lookups[sheet_col]
                    name = self.lookups.resolve_reverse(ref_type, glpi_val) if glpi_val else None
                    row_data[sheet_col] = name if name else glpi_val
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
