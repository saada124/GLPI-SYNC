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

| # | Problem | Cause | Fix | Result |
|---|---|---|---|---|
| 1 | **Listing API hides sub-categories** | `GET /ITILCategory` returns only top-level items (15) | Supplement `get_all()` with `POST /search/ITILCategory` in `lookup.py:_fetch_type` | 34 categories loaded instead of 15 |
| 2 | **Ticket_User has no date_mod** | Junction table lacks timestamp field for change detection | Skip rows with existing `Synced_At` after first sync; full fetch every time | No redundant re-processing |
| 3 | **Duplicate (ticket, user, type) combos in batch** | Sheet has redundant rows for same assignment | `seen_combos` set deduplicates tuples per batch | 400 errors eliminated |
| 4 | **Ticket_User 400 from unresolved user IDs** | Referenced User row lacks `GLPI_ID` (e.g. glpi, sync_bot) | Guard in `_resolve_ticket_assignment_ids` returns `None` → row skipped | Graceful skip, no crash |
| 5 | **UserEmail API permission denied** | API user lacks `POST /UserEmail` rights | Removed `Email` field from `config/mappings.yaml` | No silent failures |
| 6 | **Profiles field not returned by listing API** | `_profiles_id` is computed; `GET /User` skips it | Changed mapping to `profiles_id` (returned by API) | Role data syncs correctly |
| 7 | **updateCell clobbers existing sheet data** | Sending empty string overwrites values GLPI didn't return | Replaced with `updateRow` (read-merge-write) then `batchUpdateRows` | Existing data preserved |
| 8 | **6.5 minute cycle time** | Per-cell/per-row webhook calls (~5 s each) | 3-phase optimization (see table below) | **1 min 38 s (75% faster)** |
