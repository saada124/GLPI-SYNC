import requests, json

base = "http://localhost/glpi/apirest.php/"
h = {"App-Token": "v3Hf4GN4xnk7JN9zXLfEFYbQ8ABnlXqnAxjEsWs4", "Content-Type": "application/json"}
s = requests.Session()
s.headers.update(h)
r = s.post(base + "initSession", json={"user_token": "aZJJpf8A8FKjvO0hqikf150YS1Op9Np6owX6iECu"})
st = r.json()["session_token"]
s.headers.update({"Session-Token": st})

# Check Supplier search response structure
r2 = s.post(base + "search/Supplier", json={"is_deleted": 0})
data = r2.json()
print("Keys in response:", list(data.keys()))
print("First item keys:", list(data["data"][0].keys()))
print("First item full:", json.dumps(data["data"][0], indent=2))
print()

# Now try GET /Supplier (not search) to compare
r3 = s.get(base + "Supplier", headers={"Range": "items=0-999"})
print("GET /Supplier status:", r3.status_code)
if r3.status_code == 200:
    items = r3.json()
    print(f"Items count: {len(items)}")
    print("First item keys:", list(items[0].keys()))
    print("First item:", json.dumps(items[0], indent=2))
else:
    print("GET /Supplier failed:", r3.text[:200])
