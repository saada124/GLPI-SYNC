# GLPI ↔ AppSheet Sync

Bidirectional sync between GLPI (ITSM) and AppSheet/Google Sheets. AppSheet workers create tickets and mark assets; IT technicians manage them in GLPI. The sync pushes AppSheet changes to GLPI and updates existing sheet rows from GLPI.

## Performance

| Phase | Optimization | Cycle Time | Improvement |
|---|---|---|---|
| Baseline | Per-cell `updateCell` calls | ~6 min 30 s | — |
| Phase 1 | Bundled `Synced_At` into `updateRow` | ~5 min 36 s | 14% faster |
| Phase 2 | `batchUpdateRows` per tab | ~1 min 38 s | **75% faster** |

Each sync cycle processes ~60 GLPI updates and ~17 sheet row refreshes with zero errors.

## Architecture

```
AppSheet/Google Sheets  ←→  Webhook (Apps Script)  ←→  Sync Engine (Python)
                                                              ↕
                                                        GLPI REST API
```

**Key components:**

- `src/sync.py` — Orchestrator: Sheets→GLPI then GLPI→Sheets per entity
- `src/glpi_api.py` — GLPI REST client with pagination, retries, session management
- `src/sheets_client.py` — Webhook client with `batchUpdateRows` fallback
- `src/webhook/Code.gs` — Apps Script webhook deployed to the spreadsheet
- `src/lookup.py` — Reference data cache (categories, suppliers, users)
- `src/field_mappings.py` — YAML-driven field, code_lookup, and constant mapping
- `src/cache.py` — Last-sync timestamp persistence
- `config/mappings.yaml` — Entity definitions and field mappings
- `tests/` — 40 unit tests across 5 modules

## Setup

### Prerequisites

- Python 3.12+
- GLPI instance with REST API enabled
- Google Sheets with Apps Script webhook deployed

### Installation

```bash
git clone <repo>
cd GLPI-SYNC
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install -r dev-requirements.txt  # for tests
```

### Configuration

Copy `.env.example` to `.env` and fill in:

```
GLPI_URL=http://localhost/glpi/apirest.php/
GLPI_APP_TOKEN=your_app_token
GLPI_USER_TOKEN=your_user_token
SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
SHEETS_AUTH_TOKEN=glpi-sync-secret
```

### Deploy Webhook

1. Open the Apps Script project linked to your spreadsheet
2. Replace `Code.gs` with contents of `src/webhook/Code.gs`
3. Deploy → New version → Execute as: me → Who has access: Anyone
4. Copy the webhook URL into `.env`

## Usage

### Run once

```bash
venv\Scripts\python src\main.py --once
```

### Run with cache reset (full re-sync all entities)

```bash
venv\Scripts\python src\main.py --reset-cache --once
```

### Run continuously

Omit `--once` to enter polling mode (default interval: 10 minutes, configurable via `SYNC_INTERVAL_MINUTES`).

### Run tests

```bash
venv\Scripts\python -m pytest tests -v
```

## Testing

40 unit tests covering all core modules:

| Module | Tests | Key Verification |
|---|---|---|
| `field_mappings` | 11 | code_lookup resolution, constant injection, empty filtering, YAML loading |
| `lookup` | 8 | Name↔ID round-trip, case sensitivity, whitespace stripping |
| `cache` | 6 | Persistence, corrupted JSON recovery, non-serializable fallback |
| `sync` | 8 | Timestamp parsing across 5 formats, timezone handling |
| `sheets_client` | 7 | Empty-value filtering, batch fallback on failure |

All tests run in <1 second with no external dependencies.

## Entities

| Sheet Tab | GLPI ItemType | Direction | Notes |
|---|---|---|---|
| Users | User | Bidirectional | Role→profile_id code_lookup |
| Tickets | Ticket | Bidirectional | Category/supplier/requester lookups |
| Assets | Computer | Bidirectional | Category/supplier lookups |
| ticket_assignments | Ticket_User | Bidirectional | Composite key resolution (Ticket_ID + User_ID) |

## Problems & Challenges Faced

### 1. GLPI listing API hides sub-categories

The `GET /ITILCategory` endpoint returns only top-level categories (15 items). Sub-categories like *Sage X3* under *Applications* are invisible to the listing API.

**Fix:** Supplement `get_all()` with `POST /search/ITILCategory` in `lookup.py:_fetch_type`. The search API returns all items including sub-categories. Result: 34 categories loaded instead of 15.

### 2. Ticket_User junction table has no date_mod

The `Ticket_User` table lacks a `date_mod` field, making timestamp‑based change detection impossible.

**Fix:** After the first sync, rows with `Synced_At` already set are skipped entirely for this entity. `get_all_ticket_users()` is called only once, and the sheet-side timestamp comparison prevents re-processing.

### 3. Duplicate (ticket, user, type) combos within a single batch

The same Ticket_User combination can appear multiple times in a batch when the sheet has redundant rows, causing GLPI to return 400.

**Fix:** A `seen_combos` set deduplicates `(tickets_id, users_id, type)` tuples within each `_sync_direction_sheets_to_glpi` batch for ticket_assignments.

### 4. Ticket_User 400 errors from unresolved user IDs

When a User row in the sheet has no `GLPI_ID` (e.g., rows 15–16: *glpi* and *sync_bot*), `_resolve_ticket_assignment_ids` can't find the referenced user and sends invalid data to GLPI.

**Fix:** Added a `glpi_user_id` guard — if the referenced user lacks a `GLPI_ID`, return `None` from the resolver and skip the row gracefully.

### 5. UserEmail API permission denied

`_useremails[-].email` syntax is accepted by the GLPI API but silently does nothing. `POST /UserEmail` returns 403 — the API user lacks UserEmail endpoint permissions.

**Fix:** Removed the `Email` field from `config/mappings.yaml`. No UserEmail records are created during sync.

### 6. Profiles field not returned by listing API

`_profiles_id` (underscore-prefixed) is a computed field that the `GET /User` listing endpoint does not return. The sync was writing `None` back to the sheet.

**Fix:** Changed the mapping to use `profiles_id` (no underscore), which IS returned by the API. The `users_id` code_lookup maps human-readable role names to profile IDs when writing to GLPI.

### 7. Data loss from `updateCell` clobbering existing values

The first webhook implementation used per-cell `updateCell` calls. If the GLPI API didn't return a field, the Python code sent an empty string, overwriting existing sheet data.

**Fix:** Replaced with `updateRow` (read-merge-write) and later `batchUpdateRows`. The webhook reads the current row, merges only the provided values, and writes back — preserving all existing data.

### 8. Initial cycle time of 6.5 minutes

Naive per-cell and per-row webhook calls added ~5 seconds per update. With ~60 GLPI updates and ~17 sheet refreshes per cycle, this added up.

**Fix:** Three successive optimizations:
| Phase | Change | Cycle Time |
|---|---|---|
| Baseline | Per-cell `updateCell` + separate `Synced_At` | ~6 min 30 s |
| 1 | `Synced_At` bundled into `updateRow` payload | ~5 min 36 s |
| 2 | `batchUpdateRows` — all row updates per tab in one webhook call | ~1 min 38 s |
