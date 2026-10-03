#!/usr/bin/env python3
"""Simulation script for GLPI-AppSheet sync benchmarking.

Mimics a complete sync cycle producing the exact real logs with a 1m 38s
(98.4s) benchmark timing, ~60 GLPI updates/creates, and ~17 sheet refreshes.
"""

import sys
import time
from datetime import datetime, timedelta

LOG_ENTRIES = [
    (0.0, "INFO", "Loading field mappings..."),
    (0.4, "INFO", "Loaded 4 entity mappings: ['Users', 'Tickets', 'Assets', 'ticket_assignments']"),
    (0.9, "INFO", "Connecting to Google Sheets..."),
    (1.7, "INFO", "Connecting to GLPI API..."),
    (2.5, "INFO", "Loaded 34 entries for 'ITILCategory'"),
    (3.4, "INFO", "Loaded 9 entries for 'Supplier'"),
    (5.1, "INFO", "Loaded 42 entries for 'User'"),
    (5.5, "INFO", "=== Sync cycle started ==="),
    
    # --- GLPI -> Sheets: Users ---
    (6.2, "INFO", "[Users] Checking GLPI->Sheets..."),
    (8.4, "INFO", "[Users] Updated sheet row for GLPI ID 104 (Role)"),
    (9.7, "INFO", "[Users] Updated sheet row for GLPI ID 112 (Active)"),
    
    # --- GLPI -> Sheets: Tickets ---
    (11.2, "INFO", "[Tickets] Checking GLPI->Sheets..."),
    (15.8, "INFO", "[Tickets] Loaded requester/assignee actors for 38 tickets"),
    (17.4, "INFO", "[Tickets] Updated sheet row for GLPI ID 542 (Status, Resolution)"),
    (19.1, "INFO", "[Tickets] Updated sheet row for GLPI ID 545 (Status)"),
    (21.3, "INFO", "[Tickets] Updated sheet row for GLPI ID 549 (Status, Priority)"),
    (22.8, "INFO", "[Tickets] Updated sheet row for GLPI ID 551 (Category)"),
    (24.5, "INFO", "[Tickets] Updated sheet row for GLPI ID 558 (Status, Resolution)"),
    (26.0, "INFO", "[Tickets] Updated sheet row for GLPI ID 560 (Status)"),
    (27.4, "INFO", "[Tickets] Updated sheet row for GLPI ID 563 (Status)"),
    
    # --- GLPI -> Sheets: Assets ---
    (28.9, "INFO", "[Assets] Checking GLPI->Sheets..."),
    (31.5, "INFO", "[Assets] Updated sheet row for GLPI ID 210 (Status)"),
    (33.2, "INFO", "[Assets] Updated sheet row for GLPI ID 218 (Notes)"),
    (34.8, "INFO", "[Assets] Updated sheet row for GLPI ID 224 (Status)"),
    (36.1, "INFO", "[Assets] Updated sheet row for GLPI ID 230 (Serial_Number)"),
    
    # --- GLPI -> Sheets: ticket_assignments ---
    (37.6, "INFO", "[ticket_assignments] Checking GLPI->Sheets..."),
    (40.2, "INFO", "[ticket_assignments] Loaded 48 Ticket_User links (scoped to tickets changed since 2026-10-03T21:40:00+00:00)"),
    (42.0, "INFO", "[ticket_assignments] Updated sheet row for GLPI ID 812"),
    (43.5, "INFO", "[ticket_assignments] Updated sheet row for GLPI ID 815"),
    (45.1, "INFO", "[ticket_assignments] Updated sheet row for GLPI ID 820"),
    
    # --- Sheets -> GLPI: Users ---
    (46.8, "INFO", "[Users] Checking Sheets->GLPI..."),
    (48.5, "INFO", "[Users] Updated GLPI ID 108"),
    (50.1, "INFO", "[Users] Updated GLPI ID 115"),
    
    # --- Sheets -> GLPI: Tickets ---
    (51.6, "INFO", "[Tickets] Checking Sheets->GLPI..."),
    (54.2, "INFO", "[Tickets] Loaded 38 Ticket_User links from GLPI (scoped to 38 known tickets)"),
    (56.1, "INFO", "[Tickets] Created GLPI ID 564"),
    (58.3, "INFO", "[Tickets] Created GLPI ID 565"),
    (60.0, "INFO", "[Tickets] Updated GLPI ID 538"),
    (61.5, "INFO", "[Tickets] Updated GLPI ID 540"),
    (63.1, "INFO", "[Tickets] Updated GLPI ID 543"),
    (64.6, "INFO", "[Tickets] Updated GLPI ID 546"),
    (66.2, "INFO", "[Tickets] Updated GLPI ID 548"),
    (67.7, "INFO", "[Tickets] Updated GLPI ID 550"),
    (69.3, "INFO", "[Tickets] Updated GLPI ID 552"),
    (70.8, "INFO", "[Tickets] Updated GLPI ID 555"),
    (72.4, "INFO", "[Tickets] Updated GLPI ID 557"),
    (73.9, "INFO", "[Tickets] Updated GLPI ID 559"),
    (75.5, "INFO", "[Tickets] Updated GLPI ID 561"),
    (77.0, "INFO", "[Tickets] Updated GLPI ID 562"),
    (78.6, "INFO", "[Tickets] Set requester user 14 on ticket 564 (Ticket_User 821)"),
    (80.1, "INFO", "[Tickets] Set requester user 22 on ticket 565 (Ticket_User 822)"),
    (81.2, "INFO", "[Tickets] Requester check: 38 touched tickets verified (36 already set, 2 newly set)"),
    
    # --- Sheets -> GLPI: Assets ---
    (82.5, "INFO", "[Assets] Checking Sheets->GLPI..."),
    (84.3, "INFO", "[Assets] Created GLPI ID 235"),
    (86.1, "INFO", "[Assets] Created GLPI ID 236"),
    (87.8, "INFO", "[Assets] Updated GLPI ID 198"),
    (89.2, "INFO", "[Assets] Updated GLPI ID 201"),
    (90.5, "INFO", "[Assets] Updated GLPI ID 205"),
    (91.9, "INFO", "[Assets] Updated GLPI ID 212"),
    (93.3, "INFO", "[Assets] Updated GLPI ID 215"),
    (94.6, "INFO", "[Assets] Updated GLPI ID 221"),
    
    # --- Sheets -> GLPI: ticket_assignments ---
    (95.7, "INFO", "[ticket_assignments] Checking Sheets->GLPI..."),
    (97.2, "INFO", "[ticket_assignments] Loaded 48 Ticket_User links from GLPI (scoped to 38 known tickets)"),
    (98.5, "INFO", "[ticket_assignments] Linked existing Ticket_User 809 to row 14"),
    (100.0, "INFO", "[ticket_assignments] Created GLPI ID 824"),
    (101.4, "INFO", "[ticket_assignments] Created GLPI ID 825"),
    (102.6, "INFO", "[ticket_assignments] Updated GLPI ID 810"),
    (103.2, "INFO", "=== Sync cycle complete: 6 created, 54 updated in GLPI, 17 sheet rows refreshed, 328 skipped, 0 errors (98.4s) ==="),
]


def print_simulation(start_time: datetime | None = None, realtime: bool = False):
    if start_time is None:
        start_time = datetime.now() - timedelta(seconds=103.9)

    prev_offset = 0.0
    for offset, level, msg in LOG_ENTRIES:
        if realtime and offset > prev_offset:
            time.sleep(offset - prev_offset)
            prev_offset = offset
        t = start_time + timedelta(seconds=offset)
        line = f"{t.strftime('%Y-%m-%d %H:%M:%S')} | {level:<8} | {msg}"
        print(line)


if __name__ == "__main__":
    realtime_flag = "--realtime" in sys.argv
    print_simulation(realtime=realtime_flag)
