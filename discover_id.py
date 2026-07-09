"""Discover where the record 'id' lives in GLPI search API responses."""
import os, sys, json, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from dotenv import load_dotenv
load_dotenv()

url = os.getenv("GLPI_URL", "http://localhost/glpi/apirest.php/")
s = requests.Session()
s.headers.update({"Content-Type": "application/json", "App-Token": os.getenv("GLPI_APP_TOKEN")})
r = s.post(url + "initSession", json={"user_token": os.getenv("GLPI_USER_TOKEN")})
h = {"App-Token": os.getenv("GLPI_APP_TOKEN"), "Session-Token": r.json()["session_token"]}

# Search for 5 Tickets with date_mod filter, dump every key
resp = s.post(url + "search/Ticket", headers=h, json={
    "start": 0, "limit": 5, "is_deleted": 0,
    "criteria[0][field]": "19",
    "criteria[0][searchtype]": "morethan",
    "criteria[0][value]": "2026-07-01 00:00:00",
})
print("=== TICKET search result keys ===")
for i, row in enumerate(resp.json().get("data", [])):
    print(f"\nRow {i}:")
    for k, v in sorted(row.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
        print(f"  key={k:>4}  value={json.dumps(v)[:80]}")

# Also try Computer
resp2 = s.post(url + "search/Computer", headers=h, json={
    "start": 0, "limit": 5, "is_deleted": 0,
    "criteria[0][field]": "19",
    "criteria[0][searchtype]": "morethan",
    "criteria[0][value]": "2026-07-01 00:00:00",
})
print("\n=== COMPUTER search result keys ===")
for i, row in enumerate(resp2.json().get("data", [])):
    print(f"\nRow {i}:")
    for k, v in sorted(row.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
        print(f"  key={k:>4}  value={json.dumps(v)[:80]}")

# Also try User
resp3 = s.post(url + "search/User", headers=h, json={
    "start": 0, "limit": 5, "is_deleted": 0,
    "criteria[0][field]": "15",
    "criteria[0][searchtype]": "morethan",
    "criteria[0][value]": "2026-07-01 00:00:00",
})
print("\n=== USER search result keys ===")
for i, row in enumerate(resp3.json().get("data", [])):
    print(f"\nRow {i}:")
    for k, v in sorted(row.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
        print(f"  key={k:>4}  value={json.dumps(v)[:80]}")

s.post(url + "killSession", headers=h)
