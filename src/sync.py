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

# Columns that mirror GLPI clocks / derived dates. They may be refreshed when a
# *business* field changes, but must never alone trigger a sheet write.
MIRROR_SHEET_COLS = frozenset({
    "Last_Updated_At",
    "Resolved_At",
    "Purchase_Date",
    "Assigned_At",
})

# GLPI fields that are timestamps we push FROM GLPI only (never Sheets→GLPI).
MIRROR_GLPI_FIELDS = frozenset({
    "date_mod",
    "date_creation",
    "solvedate",
})

# AppSheet stores Modified_At / wall-clock fields in the workspace local zone.
# Stamping Synced_At in UTC made Modified_At look "newer" forever (UTC+1 vs UTC).
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

import os

def _sheet_tz():
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Etc/GMT-1"
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc

SHEET_TZ = _sheet_tz()


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
        # Shared Ticket_User index for a single run(), scoped by ticket ID so
        # different callers touching different (usually small) ticket sets
        # this cycle don't force a full-table re-fetch or stomp each other.
        # Reset at the top of every cycle so cross-cycle state never goes stale.
        self._ticket_user_index_cache: dict[tuple[int, int], dict] = {}
        self._ticket_user_index_covered_ids: set[int] = set()
        self._ticket_user_index_is_full: bool = False
        # Ticket GLPI IDs actually created/updated this cycle in
        # _sheets_to_glpi, so the GLPI->Sheets healing pass only re-checks
        # tickets that were touched, not the whole sheet.
        self._touched_ticket_glpi_ids: set[int] = set()

    def set_lookups(self, lookups: LookupCache) -> None:
        self.lookups = lookups

    def _get_ticket_user_index(
        self, ticket_ids: set[int] | None = None
    ) -> dict[tuple[int, int], dict]:
        """Lazily build and share the Ticket_User index for one run().

        Scoped by ticket_ids so per-cycle cost stays proportional to
        "tickets touched this cycle," not total ticket count:
          - ticket_ids=None means "give me everything" (rare — only the
            initial full sync should ask for this). Caches as a full fetch.
          - ticket_ids={...} fetches only IDs not already covered by the
            cache this cycle, merges them in, and returns the full
            accumulated index. Cheap on repeat calls with overlapping sets.

        Both directions (GLPI->Sheets actor loading, Sheets->GLPI requester
        linking) call this instead of hitting self.glpi.get_ticket_user_index()
        directly, so a cycle touching N tickets costs one filtered search
        for those N tickets, not a full-table scan, and not more than once.
        """
        if self._ticket_user_index_is_full:
            return self._ticket_user_index_cache

        if ticket_ids is None:
            # Full fetch requested — supersedes any partial cache.
            self._ticket_user_index_cache = self.glpi.get_ticket_user_index()
            self._ticket_user_index_is_full = True
            return self._ticket_user_index_cache

        missing = {int(t) for t in ticket_ids} - self._ticket_user_index_covered_ids
        if missing:
            fetched = self.glpi.get_ticket_user_index(ticket_ids=missing)
            self._ticket_user_index_cache.update(fetched)
            self._ticket_user_index_covered_ids |= missing

        return self._ticket_user_index_cache

    def run(self) -> list[dict]:
        logger.info("=== Sync cycle started ===")
        errors: list[dict] = []
        stats = {"created": 0, "updated_glpi": 0, "sheet_updates": 0, "skipped": 0, "errors": 0}
        t0 = perf_counter()
        self._ticket_user_index_cache = {}
        self._ticket_user_index_covered_ids = set()
        self._ticket_user_index_is_full = False
        self._touched_ticket_glpi_ids = set()

        # GLPI → Sheets first so technician edits land on the sheet before we
        # evaluate Sheets → GLPI dirty rows (avoids overwriting fresh GLPI work).
        for name, mapping in self.mappings.items():
            try:
                self._glpi_to_sheets(mapping, stats, errors)
            except Exception as e:
                logger.error(f"[{name}] GLPI->Sheets failed: {e}")
                errors.append({"entity": name, "direction": "glpi->sheets", "error": str(e)})

        for name, mapping in self.mappings.items():
            try:
                self._sheets_to_glpi(mapping, stats, errors)
            except Exception as e:
                logger.error(f"[{name}] Sheets->GLPI failed: {e}")
                errors.append({"entity": name, "direction": "sheets->glpi", "error": str(e)})

        elapsed = perf_counter() - t0
        now = datetime.now(timezone.utc).isoformat()
        self.cache.set_last_sync(now)
        stats["errors"] = len(errors)

        logger.info(
            f"=== Sync cycle complete: {stats['created']} created, "
            f"{stats['updated_glpi']} updated in GLPI, "
            f"{stats['sheet_updates']} sheet rows refreshed, "
            f"{stats['skipped']} skipped, "
            f"{stats['errors']} errors "
            f"({elapsed:.1f}s) ==="
        )
        return errors

    def _sheets_to_glpi(self, mapping: EntityMapping, stats: dict, errors: list[dict]) -> None:
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
        created_at_col = mapping.created_at_col

        cache_tickets = None
        cache_users = None
        ticket_user_index: dict[tuple[int, int], dict] | None = None
        pending_updates: dict[int, dict[str, Any]] = {}
        # Ticket requester (Submitted_By) is a Ticket_User type=1 link, not a Ticket field.
        pending_requesters: dict[int, int] = {}  # sheet row_idx -> glpi users_id
        requester_stats = {"unchanged": 0, "set": 0, "recovered": 0, "failed": 0}

        # Scope the Ticket_User index to tickets that already have a GLPI_ID
        # (i.e. could plausibly already have a Ticket_User row worth checking).
        # Brand-new tickets don't need pre-existing coverage — there's nothing
        # to find yet, _ensure_ticket_requester just creates the link. This is
        # what keeps the fetch proportional to "known tickets in this sheet
        # that might need a dupe-check" rather than every Ticket_User ever.
        existing_ticket_ids: set[int] = set()
        if tab in ("Tickets", "ticket_assignments") and glpi_id_col:
            for row in records:
                gid = row.get(glpi_id_col)
                if gid and str(gid).strip():
                    try:
                        existing_ticket_ids.add(int(gid))
                    except (TypeError, ValueError):
                        pass
            if tab == "ticket_assignments":
                # This tab's own GLPI_ID is the Ticket_User row id, not the
                # ticket id. Ticket_ID here is an AppSheet reference into the
                # Tickets sheet — resolve it to a GLPI ticket id via that
                # sheet's GLPI_ID column before using it to scope the index.
                existing_ticket_ids = set()
                try:
                    tickets_records = self.sheets.get_all_records("Tickets")
                except Exception:
                    tickets_records = []
                appsheet_to_glpi: dict[str, int] = {}
                for trec in tickets_records:
                    tid = str(trec.get("Ticket_ID", "")).strip()
                    gid = trec.get("GLPI_ID", "")
                    if tid and gid:
                        try:
                            appsheet_to_glpi[tid] = int(gid)
                        except (TypeError, ValueError):
                            pass
                for row in records:
                    tid = str(row.get("Ticket_ID", "")).strip()
                    if tid in appsheet_to_glpi:
                        existing_ticket_ids.add(appsheet_to_glpi[tid])

        for row_idx, row in enumerate(records, start=2):
            glpi_id = row.get(glpi_id_col) if glpi_id_col else None
            modified_at = row.get(modified_at_col) if modified_at_col else None
            synced_at = row.get(synced_at_col) if synced_at_col else ""
            created_at = row.get(created_at_col) if created_at_col else None

            should_sync = False
            has_glpi_id = bool(glpi_id and str(glpi_id).strip())

            # Never leave a row unlinked: missing GLPI_ID always means push/link.
            # (Synced_At alone used to skip forever after a partial/failed run.)
            if not has_glpi_id:
                should_sync = True
            elif not synced_at:
                should_sync = True
            elif modified_at:
                # AppSheet local Modified_At vs Synced_At. Require a real gap so
                # same-second / helper-column rewrites don't re-dirty forever.
                should_sync = self._is_newer_than(modified_at, synced_at, slack_seconds=5)
            elif created_at:
                should_sync = self._is_newer_than(created_at, synced_at, slack_seconds=5)

            if not should_sync:
                stats["skipped"] += 1
                continue

            payload = mapping.sheet_to_glpi(row)
            # Never push GLPI clock mirrors back (date_mod / solvedate / date_creation).
            for sheet_col, glpi_field in mapping.fields.items():
                if sheet_col in MIRROR_SHEET_COLS or glpi_field in MIRROR_GLPI_FIELDS:
                    payload.pop(glpi_field, None)

            for col_name, ref_type in mapping.lookups.items():
                # Submitted_By is an AppSheet User_ID, not a GLPI user name — handled below.
                if col_name == "Submitted_By":
                    continue
                raw_value = row.get(col_name)
                if raw_value and self.lookups:
                    glpi_field = mapping.fields.get(col_name)
                    resolved = self.lookups.resolve(ref_type, str(raw_value).strip())
                    if resolved is not None and glpi_field:
                        payload[glpi_field] = resolved
                    elif glpi_field and glpi_field in payload:
                        del payload[glpi_field]

            # Virtual / relation fields that are not real Ticket columns
            payload.pop("_users_id_requester", None)
            payload.pop("_users_id_assign", None)

            if tab == "Tickets":
                if cache_users is None:
                    try:
                        cache_users = self.sheets.get_all_records("Users")
                    except Exception:
                        cache_users = []
                if ticket_user_index is None:
                    try:
                        ticket_user_index = self._get_ticket_user_index(existing_ticket_ids)
                        logger.info(
                            f"[{tab}] Loaded {len(ticket_user_index)} Ticket_User links "
                            f"from GLPI (scoped to {len(existing_ticket_ids)} known tickets)"
                        )
                    except Exception as e:
                        logger.warning(f"[{tab}] Could not preload Ticket_User index: {e}")
                        ticket_user_index = {}
                req_uid = self._resolve_appsheet_user_glpi_id(
                    str(row.get("Submitted_By", "")).strip(), cache_users or []
                )
                if req_uid:
                    pending_requesters[row_idx] = req_uid
                elif str(row.get("Submitted_By", "")).strip():
                    logger.warning(
                        f"[{tab}] Row {row_idx}: Submitted_By="
                        f"{row.get('Submitted_By')!r} has no Users.GLPI_ID — requester not set"
                    )

            if tab == "ticket_assignments":
                if cache_tickets is None:
                    try:
                        cache_tickets = self.sheets.get_all_records("Tickets")
                        cache_users = self.sheets.get_all_records("Users")
                    except Exception:
                        cache_tickets = []
                        cache_users = []
                if ticket_user_index is None:
                    try:
                        ticket_user_index = self._get_ticket_user_index(existing_ticket_ids)
                        logger.info(
                            f"[{tab}] Loaded {len(ticket_user_index)} Ticket_User links "
                            f"from GLPI (scoped to {len(existing_ticket_ids)} known tickets)"
                        )
                    except Exception as e:
                        logger.warning(f"[{tab}] Could not preload Ticket_User index: {e}")
                        ticket_user_index = {}
                payload = self._resolve_ticket_assignment_ids(
                    row, payload, cache_tickets, cache_users
                )
                if payload is None:
                    stats["skipped"] += 1
                    logger.warning(
                        f"[{tab}] Row {row_idx} not pushed "
                        f"(Ticket_ID={row.get('Ticket_ID')!r}, "
                        f"User_ID={row.get('User_ID')!r}) — "
                        f"missing linked Ticket/User GLPI_ID"
                    )
                    continue

            if mapping.routing_field:
                route = mapping.get_route(row)
                if not route:
                    stats["skipped"] += 1
                    continue
                endpoint = route["endpoint"]
            else:
                endpoint = mapping.api_endpoint

            if not payload:
                continue

            if not glpi_id or str(glpi_id).strip() == "":
                # ticket_assignments: resolve existing link before POST (avoids 400 storm)
                if tab == "ticket_assignments" and ticket_user_index is not None:
                    existing = self._match_ticket_user(payload, ticket_user_index)
                    if existing and existing.get("id"):
                        # Adopt the existing GLPI id, then fall through to UPDATE
                        # so dirty sheet fields (e.g. type) still push to GLPI.
                        glpi_id = str(existing["id"])
                        if glpi_id_col:
                            pending_updates[row_idx] = {glpi_id_col: glpi_id}
                        logger.info(
                            f"[{tab}] Linked existing Ticket_User {glpi_id} to row {row_idx}"
                        )
                    else:
                        glpi_id = None

                if not glpi_id or str(glpi_id).strip() == "":
                    try:
                        new_id = self.glpi.add_item(endpoint, payload)
                        if glpi_id_col:
                            pending_updates[row_idx] = {glpi_id_col: str(new_id)}
                        if tab == "ticket_assignments" and ticket_user_index is not None:
                            try:
                                key = (int(payload["tickets_id"]), int(payload["users_id"]))
                                ticket_user_index[key] = {
                                    "id": int(new_id),
                                    "tickets_id": key[0],
                                    "users_id": key[1],
                                    **({"type": payload["type"]} if "type" in payload else {}),
                                }
                            except (KeyError, TypeError, ValueError):
                                pass
                        stats["created"] += 1
                        logger.info(f"[{tab}] Created GLPI ID {new_id}")
                    except Exception as e:
                        if tab == "ticket_assignments" and "400" in str(e):
                            recovered = self._recover_ticket_user_id(
                                payload, ticket_user_index or {}
                            )
                            if recovered:
                                glpi_id = str(recovered)
                                if glpi_id_col:
                                    pending_updates[row_idx] = {glpi_id_col: glpi_id}
                                logger.warning(
                                    f"[{tab}] Row {row_idx} already exists — "
                                    f"adopting GLPI_ID {recovered} for update"
                                )
                                # fall through to UPDATE below
                            else:
                                logger.warning(
                                    f"[{tab}] Skipping row {row_idx} (already exists): {e}"
                                )
                                stats["skipped"] += 1
                                continue
                        else:
                            logger.error(f"[{tab}] Failed to create row {row_idx}: {e}")
                            errors.append({"entity": tab, "row": row_idx, "error": str(e)})
                            continue
                    else:
                        # created successfully — stamp synced_at and go next
                        if tab == "Tickets":
                            self._touched_ticket_glpi_ids.add(int(new_id))
                        if tab == "Tickets" and row_idx in pending_requesters:
                            status = self._ensure_ticket_requester(
                                int(new_id),
                                pending_requesters[row_idx],
                                ticket_user_index if ticket_user_index is not None else {},
                            )
                            requester_stats[status] += 1
                        if synced_at_col:
                            pending_updates.setdefault(row_idx, {})[
                                synced_at_col
                            ] = self._stamp_synced_at(modified_at)
                        continue

            # UPDATE path (existing id, or adopted after link/400 recover)
            # Only PUT when GLPI business fields actually differ — Modified_At can
            # stay "dirty" from timezone skew or AppSheet rewriting helper cols.
            try:
                if self._glpi_needs_update(endpoint, int(glpi_id), payload):
                    self.glpi.update_item(endpoint, int(glpi_id), payload)
                    stats["updated_glpi"] += 1
                    logger.info(f"[{tab}] Updated GLPI ID {glpi_id}")
                else:
                    stats["skipped"] += 1
                    logger.debug(
                        f"[{tab}] GLPI ID {glpi_id} already up to date — stamping Synced_At only"
                    )
            except Exception as e:
                logger.error(f"[{tab}] Failed to update GLPI ID {glpi_id}: {e}")
                errors.append({"entity": tab, "glpi_id": glpi_id, "error": str(e)})
                continue

            if tab == "Tickets":
                self._touched_ticket_glpi_ids.add(int(glpi_id))

            if tab == "Tickets" and row_idx in pending_requesters:
                status = self._ensure_ticket_requester(
                    int(glpi_id),
                    pending_requesters[row_idx],
                    ticket_user_index if ticket_user_index is not None else {},
                )
                requester_stats[status] += 1

            if synced_at_col:
                pending_updates.setdefault(row_idx, {})[
                    synced_at_col
                ] = self._stamp_synced_at(modified_at)

        if tab == "Tickets" and self._touched_ticket_glpi_ids:
            if cache_users is None:
                try:
                    cache_users = self.sheets.get_all_records("Users")
                except Exception:
                    cache_users = []
            if ticket_user_index is None:
                try:
                    ticket_user_index = self._get_ticket_user_index(self._touched_ticket_glpi_ids)
                except Exception:
                    ticket_user_index = {}
            for row_idx, row in enumerate(records, start=2):
                if row_idx in pending_requesters:
                    continue  # already handled in dirty path above
                gid = row.get(glpi_id_col) if glpi_id_col else None
                if not gid or not str(gid).strip():
                    continue
                try:
                    gid_int = int(gid)
                except (TypeError, ValueError):
                    continue
                if gid_int not in self._touched_ticket_glpi_ids:
                    continue  # not touched this cycle — skip the healing check
                req_uid = self._resolve_appsheet_user_glpi_id(
                    str(row.get("Submitted_By", "")).strip(), cache_users or []
                )
                if not req_uid:
                    continue
                status = self._ensure_ticket_requester(
                    gid_int, req_uid, ticket_user_index or {}
                )
                requester_stats[status] += 1

            checked = sum(requester_stats.values())
            if checked:
                logger.info(
                    f"[Tickets] Requester check: {checked} touched tickets verified "
                    f"({requester_stats['unchanged'] + requester_stats['recovered']} already set, "
                    f"{requester_stats['set']} newly set"
                    + (f", {requester_stats['failed']} failed" if requester_stats["failed"] else "")
                    + ")"
                )

        if pending_updates:
            self.sheets.batch_update_rows(tab, list(pending_updates.items()))

    @staticmethod
    def _resolve_appsheet_user_glpi_id(
        appsheet_user_id: str, users_records: list
    ) -> int | None:
        """Map sheet Users.User_ID -> Users.GLPI_ID."""
        if not appsheet_user_id:
            return None
        for urec in users_records or []:
            if str(urec.get("User_ID", "")).strip() == appsheet_user_id:
                gid = urec.get("GLPI_ID", "")
                if gid not in (None, ""):
                    try:
                        return int(gid)
                    except (TypeError, ValueError):
                        return None
        return None

    def _ensure_ticket_requester(
        self,
        tickets_id: int,
        users_id: int,
        index: dict[tuple[int, int], dict],
    ) -> str:
        """Ensure Ticket_User type=1 (requester) exists for this ticket+user.

        Returns a short status string ("unchanged", "set", "recovered",
        "failed") so callers can tally results and log a single summary
        line instead of one line per ticket.
        """
        payload = {
            "tickets_id": int(tickets_id),
            "users_id": int(users_id),
            "type": 1,  # Requester
        }
        existing = self._match_ticket_user(payload, index)
        if existing and existing.get("id"):
            logger.debug(
                f"[Tickets] Requester already set on ticket {tickets_id} "
                f"(Ticket_User {existing['id']}, user {users_id})"
            )
            return "unchanged"
        try:
            new_id = self.glpi.add_item("Ticket_User", payload)
            try:
                key = (int(tickets_id), int(users_id))
                index[key] = {
                    "id": int(new_id),
                    "tickets_id": key[0],
                    "users_id": key[1],
                    "type": 1,
                }
            except (TypeError, ValueError):
                pass
            logger.info(
                f"[Tickets] Set requester user {users_id} on ticket {tickets_id} "
                f"(Ticket_User {new_id})"
            )
            return "set"
        except Exception as e:
            if "400" in str(e):
                recovered = self._recover_ticket_user_id(payload, index)
                if recovered:
                    logger.debug(
                        f"[Tickets] Requester already exists on ticket {tickets_id} "
                        f"(Ticket_User {recovered})"
                    )
                    return "recovered"
            logger.warning(
                f"[Tickets] Failed to set requester user {users_id} "
                f"on ticket {tickets_id}: {e}"
            )
            return "failed"

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

        glpi_user_id = self._resolve_appsheet_user_glpi_id(
            user_appsheet_id, users_records
        )

        if not glpi_user_id:
            logger.warning(f"[ticket_assignments] No GLPI user ID found for User_ID={user_appsheet_id}")
            return None

        if not glpi_ticket_id:
            logger.warning(f"[ticket_assignments] No GLPI ticket ID found for Ticket_ID={ticket_appsheet_id}")
            return None

        payload["tickets_id"] = glpi_ticket_id
        payload["users_id"] = glpi_user_id
        return payload

    @staticmethod
    def _match_ticket_user(
        payload: dict, index: dict[tuple[int, int], dict]
    ) -> dict | None:
        """Find an existing Ticket_User from the bulk index using payload keys.

        Prefer same type when both sides have it; otherwise fall back to the
        (tickets_id, users_id) pair so we can still adopt/link ids.
        """
        try:
            key = (int(payload["tickets_id"]), int(payload["users_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        item = index.get(key)
        if not item:
            return None
        if "type" in payload and item.get("type") not in (None, ""):
            try:
                if int(item["type"]) != int(payload["type"]):
                    # Same person may already be requester (type 1) while we
                    # want assignee (type 2) — treat as no match so create runs.
                    return None
            except (TypeError, ValueError):
                if str(item.get("type")) != str(payload.get("type")):
                    return None
        return item

    def _recover_ticket_user_id(
        self, payload: dict, index: dict[tuple[int, int], dict]
    ) -> int | None:
        """After a 400 on create, recover the existing Ticket_User id and cache it."""
        existing = self._match_ticket_user(payload, index)
        if existing and existing.get("id"):
            return int(existing["id"])

        try:
            tickets_id = int(payload["tickets_id"])
            users_id = int(payload["users_id"])
        except (KeyError, TypeError, ValueError):
            return None

        type_value = None
        if "type" in payload and payload["type"] not in (None, ""):
            try:
                type_value = int(payload["type"])
            except (TypeError, ValueError):
                type_value = None

        try:
            found = self.glpi.find_ticket_user(tickets_id, users_id, type_value)
        except Exception as e:
            logger.warning(f"[ticket_assignments] find_ticket_user failed: {e}")
            found = None

        if not found or not found.get("id"):
            return None

        index[(tickets_id, users_id)] = found
        return int(found["id"])

    def _glpi_to_sheets(self, mapping: EntityMapping, stats: dict, errors: list[dict]) -> None:
        tab = mapping.sheet_tab
        logger.info(f"[{tab}] Checking GLPI->Sheets...")

        try:
            records = self.sheets.get_all_records(tab)
        except Exception as e:
            logger.error(f"[{tab}] Failed to read sheet: {e}")
            return

        glpi_id_col = mapping.glpi_id_col
        synced_at_col = mapping.synced_at_col
        created_at_col = mapping.created_at_col

        sheet_by_glpi_id: dict[str, int] = {}
        sheet_by_key: dict[tuple[str, str], int] = {}
        for idx, sheet_row in enumerate(records, start=2):
            gid = str(sheet_row.get(glpi_id_col, "")).strip()
            if gid:
                sheet_by_glpi_id[gid] = idx
                if mapping.routing_field:
                    route = mapping.get_route(sheet_row)
                    itemtype = route["itemtype"] if route else ""
                    sheet_by_key[(itemtype, gid)] = idx

        id_maps: dict[str, dict[str, str]] = {}
        sheet_by_composite: dict[tuple, int] = {}
        ticket_actors: dict[int, dict[str, int]] = {}

        if tab in ("ticket_assignments", "Tickets"):
            for ref_tab in ("Tickets", "Users") if tab == "ticket_assignments" else ("Users",):
                try:
                    ref_records = self.sheets.get_all_records(ref_tab)
                    id_maps[ref_tab] = {}
                    ref_id_col = "User_ID" if ref_tab == "Users" else "Ticket_ID"
                    for r in ref_records:
                        gid = r.get("GLPI_ID", "")
                        if gid:
                            id_maps[ref_tab][str(gid)] = r.get(ref_id_col, "")
                except Exception:
                    id_maps[ref_tab] = {}

        if tab == "ticket_assignments":
            for idx, sr in enumerate(records, start=2):
                key = (
                    str(sr.get("Ticket_ID", "")).strip(),
                    str(sr.get("User_ID", "")).strip(),
                )
                if key[0] and key[1]:
                    sheet_by_composite[key] = idx

        glpi_records = []
        last_sync = self.cache.get_last_sync()
        is_initial = last_sync == "1970-01-01T00:00:00"

        if tab == "Tickets":
            # Requester lives on Ticket_User (type=1), not on Ticket GET payload.
            # Scope to tickets that actually changed since last_sync — this is
            # what keeps per-cycle cost proportional to "tickets that changed
            # this cycle" instead of every ticket the sheet has ever seen,
            # even as the sheet grows to thousands of historical rows.
            try:
                if is_initial:
                    # First run ever: nothing to scope against yet, so this
                    # one pass legitimately needs the full table.
                    shared_index = self._get_ticket_user_index(None)
                else:
                    try:
                        changed = self.glpi.get_changed_items("Ticket", last_sync)
                        changed_ticket_ids = {
                            int(r["id"]) for r in changed if r.get("id")
                        }
                    except Exception as e:
                        logger.warning(
                            f"[{tab}] Could not determine changed tickets ({e}); "
                            f"falling back to full Ticket_User scan this cycle"
                        )
                        changed_ticket_ids = None
                    shared_index = self._get_ticket_user_index(changed_ticket_ids)
                ticket_actors = self.glpi.get_ticket_actors(index=shared_index)
                logger.info(
                    f"[{tab}] Loaded requester/assignee actors for "
                    f"{len(ticket_actors)} tickets"
                )
            except Exception as e:
                logger.warning(f"[{tab}] Could not load ticket actors: {e}")
                ticket_actors = {}

        if mapping.routing_field:
            needed_types = set()
            for row in records:
                route = mapping.get_route(row)
                if route:
                    needed_types.add(route["itemtype"])
            if not needed_types:
                return
            for itemtype in needed_types:
                try:
                    if is_initial:
                        items = self.glpi.get_all(itemtype) or self.glpi.search(itemtype)
                    else:
                        items = self.glpi.get_changed_items(itemtype, last_sync)
                except Exception:
                    items = []
                for item in items:
                    item["_itemtype"] = itemtype
                glpi_records.extend(items)

        elif mapping.api_endpoint == "Ticket_User":
            # Load Ticket_User links so GLPI-side technician changes (type /
            # actor) can flow back to the sheet. Scoped to tickets that
            # changed since last_sync — same fix as the Tickets tab's actor
            # loading — instead of a full-table scan every cycle. Falls back
            # to a full fetch on the very first sync (nothing to diff yet).
            try:
                if is_initial:
                    index = self._get_ticket_user_index(None)
                else:
                    try:
                        changed = self.glpi.get_changed_items("Ticket", last_sync)
                        changed_ticket_ids = {
                            int(r["id"]) for r in changed if r.get("id")
                        }
                    except Exception as e:
                        logger.warning(
                            f"[{tab}] Could not determine changed tickets ({e}); "
                            f"falling back to full Ticket_User scan this cycle"
                        )
                        changed_ticket_ids = None
                    if changed_ticket_ids is None:
                        index = self._get_ticket_user_index(None)
                    else:
                        index = self._get_ticket_user_index(changed_ticket_ids)
                glpi_records = list(index.values())
            except Exception as e:
                logger.warning(f"[{tab}] Ticket_User index failed ({e}); falling back")
                glpi_records = self.glpi.get_all_ticket_users()
            if not glpi_records:
                logger.info(f"[{tab}] No Ticket_User records found")
                return
            logger.info(
                f"[{tab}] Loaded {len(glpi_records)} Ticket_User links "
                f"(scoped to tickets changed since {last_sync})"
            )
        else:
            if is_initial:
                try:
                    glpi_records = self.glpi.get_all(mapping.api_endpoint)
                except Exception:
                    try:
                        glpi_records = self.glpi.search(mapping.api_endpoint)
                    except Exception as e:
                        logger.error(f"[{tab}] GLPI query failed: {e}")
                        return
            else:
                glpi_records = self.glpi.get_changed_items(mapping.api_endpoint, last_sync)

            if not glpi_records:
                logger.info(f"[{tab}] No GLPI changes since {last_sync}")
                return

        pending_batch: dict[int, dict[str, Any]] = {}

        for glpi_row in glpi_records:
            glpi_id = glpi_row.get("id")
            if not glpi_id:
                continue

            if mapping.routing_field:
                itemtype = glpi_row.get("_itemtype", "")
                existing_row_idx = sheet_by_key.get((itemtype, str(glpi_id)))
            else:
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
                    continue

            sheet_row = records[existing_row_idx - 2]

            # Build the sheet payload from GLPI, then write ONLY columns that
            # actually differ. date_mod alone is not enough: followups/comments
            # bump date_mod without changing mapped fields, which used to rewrite
            # only synced_at on every cycle.
            row_data: dict[str, Any] = {}
            for sheet_col, glpi_field in mapping.fields.items():
                if not glpi_field:
                    continue
                # Virtual relation fields — filled from Ticket_User actors below
                if glpi_field.startswith("_users_id_"):
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
                        elif glpi_val not in ("", None):
                            row_data[sheet_col] = glpi_val
                else:
                    row_data[sheet_col] = glpi_val

            if tab == "Tickets" and ticket_actors:
                try:
                    actors = ticket_actors.get(int(glpi_id), {})
                except (TypeError, ValueError):
                    actors = {}
                req_glpi_uid = actors.get("requester")
                if req_glpi_uid is not None:
                    appsheet_uid = id_maps.get("Users", {}).get(str(req_glpi_uid), "")
                    if appsheet_uid:
                        row_data["Submitted_By"] = appsheet_uid
                # Optional: surface primary assignee if the sheet has Assigned_To
                asg_glpi_uid = actors.get("assignee")
                if asg_glpi_uid is not None and "Assigned_To" in (sheet_row or {}):
                    appsheet_aid = id_maps.get("Users", {}).get(str(asg_glpi_uid), "")
                    if appsheet_aid:
                        row_data["Assigned_To"] = appsheet_aid

            if tab == "ticket_assignments":
                raw_tid = glpi_row.get("tickets_id")
                if raw_tid is not None:
                    mapped_tid = id_maps.get("Tickets", {}).get(str(raw_tid), "")
                    if mapped_tid:
                        row_data["Ticket_ID"] = mapped_tid
                raw_uid = glpi_row.get("users_id")
                if raw_uid is not None:
                    mapped_uid = id_maps.get("Users", {}).get(str(raw_uid), "")
                    if mapped_uid:
                        row_data["User_ID"] = mapped_uid

            # Business fields can trigger a write. Mirror timestamps only ride along
            # when a business field (or missing GLPI_ID) actually changed.
            business_changed: dict[str, Any] = {}
            mirror_changed: dict[str, Any] = {}

            for col, new_val in row_data.items():
                if synced_at_col and col == synced_at_col:
                    continue
                # created_at helper: fill only if empty (not a dirty signal)
                if created_at_col and col == created_at_col:
                    if not str(sheet_row.get(created_at_col, "") or "").strip():
                        if new_val not in (None, ""):
                            mirror_changed[col] = self._format_sheet_value(new_val)
                    continue

                old_val = sheet_row.get(col, "")
                if glpi_id_col and col == glpi_id_col:
                    if not str(old_val or "").strip() and new_val not in (None, ""):
                        business_changed[col] = str(new_val)
                    continue

                if not self._values_differ(old_val, new_val):
                    continue

                formatted = self._format_sheet_value(new_val)
                if self._is_mirror_col(col, mapping):
                    mirror_changed[col] = formatted
                else:
                    business_changed[col] = formatted

            # GLPI_ID staged earlier via composite match (assignments)
            if existing_row_idx in pending_batch and glpi_id_col:
                staged_gid = pending_batch[existing_row_idx].get(glpi_id_col)
                if staged_gid and not str(sheet_row.get(glpi_id_col, "") or "").strip():
                    business_changed[glpi_id_col] = staged_gid

            # Missing GLPI_ID alone is worth a heal write
            if (
                glpi_id_col
                and not str(sheet_row.get(glpi_id_col, "") or "").strip()
                and glpi_id not in (None, "")
            ):
                business_changed.setdefault(glpi_id_col, str(glpi_id))

            if not business_changed:
                # Mirror-only / format-only noise → do not touch the sheet.
                # Drop any staged pending for this row (e.g. composite GLPI_ID
                # that somehow didn't promote — shouldn't happen).
                if existing_row_idx in pending_batch:
                    staged = pending_batch[existing_row_idx]
                    # Keep a pure GLPI_ID heal if that was staged
                    if glpi_id_col and glpi_id_col in staged and not str(
                        sheet_row.get(glpi_id_col, "") or ""
                    ).strip():
                        heal = {glpi_id_col: staged[glpi_id_col]}
                        if synced_at_col:
                            heal[synced_at_col] = self._stamp_synced_at(
                                sheet_row.get(mapping.modified_at_col)
                                if mapping.modified_at_col
                                else None
                            )
                        pending_batch[existing_row_idx] = heal
                        stats["sheet_updates"] += 1
                        logger.info(
                            f"[{tab}] Healed GLPI_ID on sheet row for GLPI ID {glpi_id}"
                        )
                    else:
                        del pending_batch[existing_row_idx]
                        stats["skipped"] += 1
                else:
                    stats["skipped"] += 1
                continue

            changed = dict(business_changed)
            # Refresh mirrors only alongside a real business update
            changed.update(mirror_changed)

            if synced_at_col:
                changed[synced_at_col] = self._stamp_synced_at(
                    sheet_row.get(mapping.modified_at_col) if mapping.modified_at_col else None
                )
            if created_at_col and not str(sheet_row.get(created_at_col, "") or "").strip():
                if created_at_col not in changed:
                    seed = (
                        glpi_row.get("date_creation")
                        or glpi_row.get("date_mod")
                        or self._stamp_synced_at(None)
                    )
                    changed[created_at_col] = self._format_sheet_value(seed)

            if existing_row_idx in pending_batch:
                pending_batch[existing_row_idx].update(changed)
            else:
                pending_batch[existing_row_idx] = changed
            stats["sheet_updates"] += 1
            changed_cols = ", ".join(
                c
                for c in business_changed
                if c not in (synced_at_col, created_at_col, glpi_id_col)
            ) or ", ".join(business_changed.keys())
            logger.info(
                f"[{tab}] Updated sheet row for GLPI ID {glpi_id}"
                + (f" ({changed_cols})" if changed_cols else "")
            )

        if pending_batch:
            self.sheets.batch_update_rows(tab, list(pending_batch.items()))

    def _glpi_needs_update(self, endpoint: str, glpi_id: int, payload: dict) -> bool:
        """True if any payload field differs from the live GLPI item.

        Avoids useless PUTs when the row only looks dirty because of Synced_At /
        Modified_At timezone skew or AppSheet rewriting helper columns.
        """
        if not payload:
            return False
        try:
            current = self.glpi.get_item(endpoint, glpi_id) or {}
        except Exception as e:
            logger.warning(
                f"Could not GET {endpoint}/{glpi_id} for diff check ({e}); will PUT"
            )
            return True

        # Fields that are not meaningful for equality, or are set via Ticket_User
        skip_keys = {
            "id",
            "_users_id_requester",
            "_users_id_assign",
            "date_mod",
            "date_creation",
            "solvedate",
            "date",
        }

        for key, new_val in payload.items():
            if key in skip_keys or key.startswith("_"):
                continue
            if new_val is None or new_val == "":
                continue  # don't treat blank sheet side as a wipe/diff
            old_val = current.get(key, "")
            # GLPI often returns numeric ids as strings
            if self._values_differ(old_val, new_val):
                logger.debug(
                    f"{endpoint}/{glpi_id} field '{key}' differs: "
                    f"glpi={old_val!r} sheet={new_val!r}"
                )
                return True
        return False

    @classmethod
    def _is_newer_than(cls, newer: Any, older: Any, slack_seconds: int = 5) -> bool:
        """True if `newer` is strictly after `older` by more than slack_seconds."""
        try:
            a = cls._parse_timestamp(str(newer))
            b = cls._parse_timestamp(str(older))
        except (ValueError, TypeError):
            return False
        return (a - b).total_seconds() > slack_seconds

    @classmethod
    def _stamp_synced_at(cls, modified_at: Any = None) -> str:
        """Wall-clock stamp in AppSheet's timezone, never earlier than Modified_At.

        Writing UTC while AppSheet stores local Modified_At made every row look
        dirty forever (e.g. Modified 15:03 local > Synced 14:03 UTC).

        Uses a generous buffer so AppSheet automations that rewrite Modified_At
        when Synced_At is written cannot immediately re-dirty the row.
        """
        from datetime import timedelta

        now = datetime.now(SHEET_TZ).replace(microsecond=0)
        candidates = [now]
        if modified_at not in (None, ""):
            try:
                md = cls._parse_timestamp(str(modified_at))
                # treat naive Modified_At as already in sheet local zone
                if md.tzinfo is None:
                    md = md.replace(tzinfo=SHEET_TZ)
                else:
                    md = md.astimezone(SHEET_TZ)
                candidates.append(md)
            except (ValueError, TypeError):
                pass
        stamp = max(candidates)
        # Buffer against AppSheet setting Modified_At ≈ write time on helper updates
        stamp = stamp + timedelta(seconds=120)
        return stamp.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _is_mirror_col(col: str, mapping: EntityMapping) -> bool:
        """True for timestamp-mirror columns that must not alone dirty reverse sync."""
        if col in MIRROR_SHEET_COLS:
            return True
        glpi_field = (mapping.fields or {}).get(col, "")
        if glpi_field in MIRROR_GLPI_FIELDS:
            return True
        # Convention: * _At / *_Date sheet cols are mirrors except control helpers
        if col.endswith("_At") or col.endswith("_Date"):
            if col not in ("Modified_At", "Synced_At", "Created_At"):
                return True
        return False

    @classmethod
    def _format_sheet_value(cls, value: Any) -> Any:
        """Normalize values written back to the sheet (stable local timestamp format)."""
        if value is None or value == "":
            return value
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=SHEET_TZ)
            return dt.astimezone(SHEET_TZ).strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, str):
            try:
                dt = cls._parse_timestamp(value)
                return dt.astimezone(SHEET_TZ).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                return value
        return value

    @classmethod
    def _values_differ(cls, old: Any, new: Any) -> bool:
        """True when sheet value and GLPI-derived value are meaningfully different."""
        if new is None or new == "":
            return False  # don't treat empty GLPI as a change wipe
        if old is None or old == "":
            return True

        # Datetime-aware compare (format noise must not count as a change)
        old_dt = cls._try_parse_timestamp(old)
        new_dt = cls._try_parse_timestamp(new)
        if old_dt is not None and new_dt is not None:
            return int(old_dt.timestamp()) != int(new_dt.timestamp())

        # numeric-ish compare (status codes, type ints, bools, etc.)
        try:
            if float(old) == float(new):
                return False
        except (TypeError, ValueError):
            pass

        old_s = str(old).strip()
        new_s = str(new).strip()
        if old_s.lower() in ("true", "false", "1", "0") and new_s.lower() in (
            "true",
            "false",
            "1",
            "0",
        ):
            old_bool = old_s.lower() in ("true", "1")
            new_bool = new_s.lower() in ("true", "1")
            return old_bool != new_bool

        return old_s != new_s

    @classmethod
    def _try_parse_timestamp(cls, value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            return cls._parse_timestamp(str(value))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime:
        raw = str(ts).strip()
        # Handle trailing Z / fractional seconds from GLPI
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if "." in raw and ("T" in raw or " " in raw):
            # strip fractional seconds but keep timezone if present
            head, rest = raw.split(".", 1)
            tz = ""
            for sep in ("+", "-"):
                # timezone after fraction: 20:39:22.000+00:00 or .000Z already handled
                idx = rest.find(sep)
                if idx > 0 and ":" in rest[idx:]:
                    tz = rest[idx:]
                    break
            raw = head + tz

        for fmt in (
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%m/%d/%y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    # Naive sheet timestamps are AppSheet local, not UTC
                    dt = dt.replace(tzinfo=SHEET_TZ)
                return dt
            except ValueError:
                continue
        raise ValueError(f"Cannot parse timestamp: {ts}")