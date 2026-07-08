import requests

base = "http://localhost/glpi/apirest.php/"
h = {"App-Token": "v3Hf4GN4xnk7JN9zXLfEFYbQ8ABnlXqnAxjEsWs4", "Content-Type": "application/json"}
s = requests.Session()
s.headers.update(h)
r = s.post(base + "initSession", json={"user_token": "aZJJpf8A8FKjvO0hqikf150YS1Op9Np6owX6iECu"})
st = r.json()["session_token"]
s.headers.update({"Session-Token": st})

types = ["Supplier", "ITILCategory", "ComputerType", "User"]
tests = [
    ("POST", {"is_deleted": 0}, "post:is_deleted"),
    ("POST", {"start": 0, "limit": 9999, "is_deleted": 0}, "post:full"),
    ("GET", None, "get:none"),
    ("GET", {"start": 0, "limit": 9999, "is_deleted": 0}, "get:params"),
]

for t in types:
    print(f"--- {t} ---")
    for method, body, label in tests:
        try:
            if method == "POST":
                r2 = s.post(base + "search/" + t, json=body or {})
            else:
                r2 = s.get(base + "search/" + t, params=body or {})
            data = r2.json()
            count = len(data.get("data", []))
            print(f"  {label}: OK ({count} items)")
            if count > 0:
                for item in data["data"]:
                    print(f"    {item.get('id')}: {item.get('name')}")
        except Exception as e:
            print(f"  {label}: FAILED")
