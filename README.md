# 🚀 GLPI ↔ AppSheet Sync Engine

> **Seamless, ultra-fast, bidirectional synchronization between GLPI ITSM and Google Sheets / AppSheet.**

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![GLPI](https://img.shields.io/badge/GLPI-API_REST-orange.svg)](https://glpi-project.org/)
[![Google Sheets](https://img.shields.io/badge/Google_Sheets-Apps_Script-green.svg)](https://workspace.google.com/)
[![Tests](https://img.shields.io/badge/Tests-47%2F47_Passed-success.svg)](#-rock-solid-testing)
[![Speed](https://img.shields.io/badge/Performance-75%25_Faster-brightgreen.svg)](#-lightning-fast-performance)

---

### 💡 What is this?
Field technicians use **AppSheet** on mobile to log hardware and tickets on the go. IT administrators manage tickets in **GLPI**.  
This sync engine bridges both worlds in real-time — pushing updates bidirectionally with zero data loss or infinite loop issues!

> 🇫🇷 **Vous débutez ?**  
> Consultez notre guide pas à pas en français : **[GUIDE_CONFIGURATION_GLPI.md](GUIDE_CONFIGURATION_GLPI.md)** (ou en version texte **[GUIDE_CONFIGURATION_GLPI.txt](GUIDE_CONFIGURATION_GLPI.txt)**).

---

## ⚡ Lightning-Fast Performance

We optimized the sync pipeline from 6+ minutes down to **1.5 minutes**:

| Phase | Strategy | Cycle Time | Boost |
| :--- | :--- | :--- | :--- |
| **Baseline** | Per-cell `updateCell` calls | ~6 min 30 s | 🐢 Baseline |
| **Phase 1** | Bundled `Synced_At` timestamps into `updateRow` | ~5 min 36 s | ⚡ +14% |
| **Phase 2** | `batchUpdateRows` bulk API requests | **~1 min 38 s** | 🚀 **+75% Faster** |

🔥 *Syncs ~60 GLPI updates & ~17 sheet refreshes per cycle with 0 errors!*

---

## 🏗️ System Architecture

```
📱 AppSheet / Google Sheets
         ↕️  (Apps Script Webhook)
🐍 Python Sync Orchestrator
         ↕️  (GLPI REST API)
🖥️ GLPI ITSM Platform
```

### 📦 Key Components

- 🎮 `src/sync.py` — Orchestrates bidirectional flows, diffing, timezone buffers & state tracking.
- 🔑 `src/glpi_api.py` — Resilient GLPI API client with auto-pagination, retries & bulk `Ticket_User` indexing.
- ⚡ `src/sheets_client.py` — Google Apps Script Webhook client with automatic batch fallbacks.
- 📜 `src/webhook/Code.gs` — Apps Script engine deployed directly inside your Google Sheet.
- 🔍 `src/lookup.py` — In-memory caching for GLPI categories, suppliers, and users.
- 🗺️ `src/field_mappings.py` — YAML-driven schema mapper with category routing & normalization.
- 🌱 `seed_glpi_dropdowns.py` — 1-click seeder for GLPI categories, computer types & suppliers.

---

## 🎮 5-Minute Quick Start

### 1️⃣ Clone & Setup Virtual Environment
```bash
git clone <repo>
cd GLPI-SYNC
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install -r dev-requirements.txt
```

### 2️⃣ Configure `.env`
Copy `.env.example` to `.env` and fill in your keys:
```env
GLPI_URL=http://localhost/glpi/apirest.php/
GLPI_APP_TOKEN=your_app_token_here
GLPI_USER_TOKEN=your_user_token_here

SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/your-script-id/exec
SHEETS_AUTH_TOKEN=glpi-sync-secret

APP_TIMEZONE=Etc/GMT-1
SYNC_INTERVAL_MINUTES=10
```

### 3️⃣ Seed GLPI Dropdowns (Optional but Recommended!)
Automatically populate GLPI with ITIL Categories, Computer Types & Suppliers:
```bash
venv\Scripts\python seed_glpi_dropdowns.py
```

### 4️⃣ Launch Sync!
- 🧪 **Single Test Run:**
  ```bash
  venv\Scripts\python src\main.py --once
  ```
- 🔄 **Continuous Background Mode:**
  Double-click **`run_sync.bat`** or run:
  ```bash
  venv\Scripts\python src\main.py
  ```

---

## 🧪 Rock-Solid Testing

47 automated unit tests covering all core modules with zero external dependencies (<0.3s execution time):

```
tests/test_cache.py .......... [12%]
tests/test_field_mappings.py .. [51%]
tests/test_lookup.py ......... [68%]
tests/test_sheets_client.py ... [82%]
tests/test_sync.py ............ [100%]

================ 47 passed in 0.25s ================
```

Run tests anytime with:
```bash
venv\Scripts\python -m pytest tests -v
```

---

## 🔄 Supported Entities & Flows

| Sheet Tab | GLPI Entity | Direction | Smart Feature |
| :--- | :--- | :--- | :--- |
| 👥 **Users** | `User` | 🔄 Bidirectional | Role ↔ profile_id mapping |
| 🎫 **Tickets** | `Ticket` | 🔄 Bidirectional | Category, supplier & requester resolution |
| 💻 **Assets** | `Computer` / `Monitor` / `Cable` | 🔄 Bidirectional | Smart category routing & fallback rules |
| 🔗 **Assignments** | `Ticket_User` | 🔄 Bidirectional | Composite key indexing & duplicate prevention |

---

## 🛠️ Problems & Challenges Solved

Here are the real technical challenges we solved to make this integration fast, safe, and bug-free:

| # | What was happening? (Problem) | Why did it happen? (Cause) | How did we fix it? (Solution) | Result |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 🐢 **Sync took over 6.5 minutes** | Sending row updates one by one across the network | Grouped rows into single bulk updates (`batchUpdateRows`) | 🚀 **Sync runs in 1.5 minutes (75% faster)** |
| 2 | 🔄 **Infinite sync loops** | Google Sheets used local time (CET) while GLPI used UTC time | Added timezone matching (`APP_TIMEZONE`) and a 2-minute safety buffer | 🛑 **Sync stops when data hasn't changed** |
| 3 | 📶 **Too many network requests** | Checking ticket assignments made 1 network call per ticket | Created a single bulk index list to fetch all assignments at once | ⚡ **Saved 100+ unnecessary network calls** |
| 4 | ⚠️ **Error 400 on ticket assignments** | App tried to re-assign users who were already assigned | Added a pre-check (`_match_ticket_user`) to skip existing links | 🛡️ **Zero assignment errors** |
| 5 | 🙈 **Sub-categories were missing** | Standard API only listed top categories (15 total) | Combined list API with search API (`lookup.py`) to fetch sub-items | 📂 **All 34 categories load correctly** |
| 6 | 💻 **All equipment saved as "Computer"** | Screens and cables had no routing rules in GLPI | Added smart category routing (Laptop -> Computer, Screen -> Monitor, etc.) | 🎯 **Assets are saved in correct GLPI categories** |
| 7 | ❌ **Errors when updating dates** | GLPI rejected system dates (`date_mod`, `solvedate`) | Filtered out system dates so only real user changes get pushed | 🛑 **No more read-only date errors** |
| 8 | 🗑️ **Data getting erased in Sheets** | Updating single cells could accidentally overwrite existing data | Switched to full-row read-merge-write before saving | 🔒 **Sheet data is never lost** |
| 9 | 🚫 **Permission denied crashes** | GLPI user account had no permission to edit email tables | Removed restricted email fields from direct mapping | 🛡️ **No permission crashes** |
| 10 | 💥 **Crash on missing user or ticket** | App crashed if a ticket pointed to a missing user ID | Added safety checks to skip broken links and log a warning | 🛡️ **Sync keeps running without crashing** |

---

<p align="center">
  <b>Built with ❤️ for seamless ITSM & AppSheet integration.</b>
</p>
