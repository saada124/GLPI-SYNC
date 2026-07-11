from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
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
    ):
        self.glpi = glpi
        self.sheets = sheets
        self.mappings = mappings
        self.cache = cache
        self.lookups = None
        self._errors: list[dict] = []

    def set_lookups(self, lookups: LookupCache) -> None:
        self.lookups = lookups

    def run(self) -> None:
        logger.info("=== Sync cycle started ===")
        self._errors.clear()
        self._cycle_stats: dict[str, int] = {
            "created": 0, "updated_glpi": 0,
            "sheet_updates": 0, "skipped": 0,
            "errors": 0,
        }

        t0 = perf_counter()

        for name, mapping in self.mappings.items():
            try:
                self._sync_direction_sheets_to_glpi(mapping)
            except Exception as e:
                logger.error(f"[{name}] Sheets->GLPI failed: {e}")
                self._errors.append({"entity": name, "direction": "sheets->glpi", "error": str(e)})

        for name, mapping in self.mappings.items():
            try:
                self._sync_direction_glpi_to_sheets(mapping)
            except Exception as e:
                logger.error(f"[{name}] GLPI->Sheets failed: {e}")
                self._errors.append({"entity": name, "direction": "glpi->sheets", "error": str(e)})

        elapsed = perf_counter() - t0

        now = datetime.now(timezone.utc).isoformat()
        self.cache.set_last_sync(now)

        self._cycle_stats["errors"] = len(self._errors)
        s = self._cycle_stats
        logger.info(
            f"=== Sync cycle complete: {s['created']} created, "
            f"{s['updated_glpi']} updated in GLPI, "
            f"{s['sheet_updates']} sheet rows refreshed, "
            f"{s['skipped']} skipped, "
            f"{s['errors']} errors "
            f"({elapsed:.1f}s) ==="
        )

        return self._errors

    def _sync_direction_sheets_to_glpi(self, mapping: EntityMapping) -> None:
        tab = mapping.sheet_tab
        logger.info(f"[{tab}] Checking Sheets->GLPI...")

        try:
            records = self.sheets.get_all_records(tab)
        except Exception as e:
            logger.error(f"[{tab}] Failed to read sheet: {e}")
            return

        glpi_id_col = mapping.glpi_id_col
        synced_at_col = mapping.synced_at_col
        modified_at_col = mapping.modified_at_col

        # Cache for ticket_assignments ID resolution (read once, not per row)
        cache_tickets = None
        cache_users = None
        seen_combos = set()
        pending_updates: dict[int, dict[str, Any]] = {}

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

            # Apply text->ID lookups for reference columns
            for col_name, ref_type in mapping.lookups.items():
                raw_value = row.get(col_name)
                if raw_value and self.lookups:
                    glpi_field = mapping.fields.get(col_name)
                    resolved = self.lookups.resolve(ref_type, str(raw_value).strip())
                    if resolved is not None and glpi_field:
                        payload[glpi_field] = resolved
                    elif glpi_field and glpi_field in payload:
                        del payload[glpi_field]

            # Special handling: ticket_assignments resolves IDs via GLPI_ID columns
            if tab == "ticket_assignments":
                if cache_tickets is None:
                    try:
                        cache_tickets = self.sheets.get_all_records("Tickets")
                        cache_users = self.sheets.get_all_records("Users")
                    except Exception:
                        cache_tickets = []
                        cache_users = []
                payload = self._resolve_ticket_assignment_ids(row, payload, cache_tickets, cache_users)
                if payload is None:
                    self._cycle_stats["skipped"] += 1
                    continue
                # Skip duplicate combos within the same batch
                combo_key = (payload.get("tickets_id"), payload.get("users_id"), payload.get("type", 2))
                if combo_key in seen_combos:
                    logger.warning(f"[ticket_assignments] Skipping duplicate combo {combo_key} in batch")
                    self._cycle_stats["skipped"] += 1
                    continue
                seen_combos.add(combo_key)

            if not payload:
                continue

            if not glpi_id or str(glpi_id).strip() == "":
                try:
                    new_id = self.glpi.add_item(mapping.api_endpoint, payload)
                    if glpi_id_col:
                        pending_updates[row_idx] = {glpi_id_col: str(new_id)}
                    self._cycle_stats["created"] += 1
                    logger.info(f"[{tab}] Created GLPI ID {new_id}")
                except Exception as e:
                    if tab == "ticket_assignments" and "400" in str(e):
                        logger.warning(f"[{tab}] Skipping row {row_idx} (already exists or GLPI rejected): {e}")
                        self._cycle_stats["skipped"] += 1
                        continue
                    logger.error(f"[{tab}] Failed to create row {row_idx}: {e}")
                    self._errors.append({"entity": tab, "row": row_idx, "error": str(e)})
                    continue
            else:
                try:
                    self.glpi.update_item(mapping.api_endpoint, int(glpi_id), payload)
                    self._cycle_stats["updated_glpi"] += 1
                    logger.info(f"[{tab}] Updated GLPI ID {glpi_id}")
                except Exception as e:
                    logger.error(f"[{tab}] Failed to update GLPI ID {glpi_id}: {e}")
                    self._errors.append({"entity": tab, "glpi_id": glpi_id, "error": str(e)})
                    continue

            if synced_at_col:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                pending_updates.setdefault(row_idx, {})[synced_at_col] = now

        if pending_updates:
            self.sheets.batch_update_rows(tab, list(pending_updates.items()))

    def _resolve_ticket_assignment_ids(
        self, row: dict, payload: dict, tickets_records: list, users_records: list
    ) -> dict | None:
        ticket_appsheet_id = str(row.get("Ticket_ID", "")).strip()
        user_appsheet_id = str(row.get("User_ID", "")).strip()

        if not user_appsheet_id:
            logger.warning(f"[ticket_assignments] Skipping row: User_ID is empty")
            return None

        if not tickets_records:
            logger.error("[ticket_assignments] Tickets sheet data is empty")
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
            for urec in users_records or []:
                if str(urec.get("User_ID", "")).strip() == user_appsheet_id:
                    gid = urec.get("GLPI_ID", "")
                    if gid:
                        glpi_user_id = int(gid)
                    break

        if not glpi_user_id:
            logger.warning(
                f"[ticket_assignments] No GLPI user ID found for User_ID={user_appsheet_id}"
            )
            return None

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
        payload["users_id"] = glpi_user_id

        return payload

    def _sync_direction_glpi_to_sheets(self, mapping: EntityMapping) -> None:
        tab = mapping.sheet_tab
        logger.info(f"[{tab}] Checking GLPI->Sheets...")

        if mapping.api_endpoint == "Ticket_User":
            glpi_records = self.glpi.get_all_ticket_users()
            if not glpi_records:
                logger.info(f"[{tab}] No Ticket_User records found")
                return
        else:
            last_sync = self.cache.get_last_sync()
            is_initial = last_sync == "1970-01-01T00:00:00"
            if is_initial:
                try:
                    glpi_records = self.glpi.get_all(mapping.api_endpoint)
                except Exception:
                    try:
                        glpi_records = self.glpi.search(mapping.api_endpoint)
                    except Exception as e:
                        logger.error(f"[{tab}] GLPI query failed: {e}")
                        return
                if not glpi_records:
                    logger.info(f"[{tab}] No GLPI records found")
                    return
            else:
                glpi_records = self.glpi.get_changed_items(mapping.api_endpoint, last_sync)
                if not glpi_records:
                    logger.info(f"[{tab}] No GLPI changes since {last_sync}")
                    return

        try:
            records = self.sheets.get_all_records(tab)
        except Exception as e:
            logger.error(f"[{tab}] Failed to read sheet: {e}")
            return

        glpi_id_col = mapping.glpi_id_col
        synced_at_col = mapping.synced_at_col
        tolerance_s = 2

        pending_batch: dict[int, dict[str, Any]] = {}
        sheet_by_glpi_id: dict[str, int] = {}
        for idx, sheet_row in enumerate(records, start=2):
            gid = str(sheet_row.get(glpi_id_col, "")).strip()
            if gid:
                sheet_by_glpi_id[gid] = idx

        id_maps: dict[str, dict[str, str]] = {}
        sheet_by_composite: dict[tuple, int] = {}
        if tab == "ticket_assignments":
            for ref_tab in ("Tickets", "Users"):
                try:
                    ref_records = self.sheets.get_all_records(ref_tab)
                    id_maps[ref_tab] = {}
                    ref_id_col = ref_tab[:-1] + "_ID"
                    for r in ref_records:
                        gid = r.get("GLPI_ID", "")
                        if gid:
                            id_maps[ref_tab][str(gid)] = r.get(ref_id_col, "")
                except Exception:
                    id_maps[ref_tab] = {}
            for idx, sr in enumerate(records, start=2):
                key = (
                    str(sr.get("Ticket_ID", "")).strip(),
                    str(sr.get("User_ID", "")).strip(),
                )
                if key[0] and key[1]:
                    sheet_by_composite[key] = idx

        for glpi_row in glpi_records:
            glpi_id = glpi_row.get("id")
            if not glpi_id:
                continue

            existing_row_idx = sheet_by_glpi_id.get(str(glpi_id))
            if not existing_row_idx:
                if tab == "ticket_assignments":
                    raw_tid = glpi_row.get("tickets_id")
                    raw_uid = glpi_row.get("users_id")
                    ticket_as_id = id_maps.get("Tickets", {}).get(str(raw_tid), "")
                    user_as_id = id_maps.get("Users", {}).get(str(raw_uid), "")
                    key = (ticket_as_id, user_as_id)
                    existing_row_idx = sheet_by_composite.get(key)
                    if existing_row_idx:
                        pending_batch.setdefault(existing_row_idx, {})[glpi_id_col] = str(glpi_id)
                if not existing_row_idx:
                    continue  # only update rows that came from AppSheet

            date_mod = glpi_row.get("date_mod", "")
            if synced_at_col:
                existing_synced = records[existing_row_idx - 2].get(synced_at_col, "")
                if existing_synced:
                    if tab == "ticket_assignments" and not date_mod:
                        self._cycle_stats["skipped"] += 1
                        continue  # junction table has no modification timestamp
                    if date_mod:
                        try:
                            glpi_ts = self._parse_timestamp(date_mod)
                            sheet_ts = self._parse_timestamp(existing_synced)
                            if abs((glpi_ts - sheet_ts).total_seconds()) <= tolerance_s:
                                self._cycle_stats["skipped"] += 1
                                continue
                        except (ValueError, TypeError):
                            pass

            row_data: dict[str, Any] = {}
            for sheet_col, glpi_field in mapping.fields.items():
                if not glpi_field:
                    continue
                glpi_val = glpi_row.get(glpi_field, "")

                if sheet_col in mapping.code_lookups:
                    rev = {v: k for k, v in mapping.code_lookups[sheet_col].items()}
                    row_data[sheet_col] = rev.get(glpi_val, glpi_val)
                elif sheet_col in mapping.lookups and self.lookups:
                    ref_type = mapping.lookups[sheet_col]
                    if glpi_val or glpi_val == 0:
                        name = self.lookups.resolve_reverse(ref_type, glpi_val)
                        if name:
                            row_data[sheet_col] = name
                        elif glpi_val:
                            row_data[sheet_col] = glpi_val
                else:
                    row_data[sheet_col] = glpi_val

            if tab == "ticket_assignments":
                raw_tid = glpi_row.get("tickets_id")
                if raw_tid is not None:
                    row_data["Ticket_ID"] = id_maps.get("Tickets", {}).get(str(raw_tid), row_data.get("Ticket_ID", ""))
                raw_uid = glpi_row.get("users_id")
                if raw_uid is not None:
                    row_data["User_ID"] = id_maps.get("Users", {}).get(str(raw_uid), row_data.get("User_ID", ""))

            if synced_at_col:
                row_data[synced_at_col] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if existing_row_idx in pending_batch:
                pending_batch[existing_row_idx].update(row_data)
            else:
                pending_batch[existing_row_idx] = row_data
            self._cycle_stats["sheet_updates"] += 1
            logger.info(f"[{tab}] Updated sheet row for GLPI ID {glpi_id}")

        if pending_batch:
            self.sheets.batch_update_rows(tab, list(pending_batch.items()))

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
