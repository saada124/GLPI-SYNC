"""Modify a GLPI ticket to test GLPI->Sheets sync direction."""
import requests

base = "http://localhost/glpi/apirest.php/"
h = {"App-Token": "v3Hf4GN4xnk7JN9zXLfEFYbQ8ABnlXqnAxjEsWs4", "Content-Type": "application/json"}
s = requests.Session()
s.headers.update(h)
r = s.post(base + "initSession", json={"user_token": "aZJJpf8A8FKjvO0hqikf150YS1Op9Np6owX6iECu"})
st = r.json()["session_token"]
s.headers.update({"Session-Token": st})

t = s.get(base + "Ticket/1").json()
old_name = t.get("name")
print(f"Before: {old_name}")

new_name = old_name + " [SYNC TEST]"
s.put(base + "Ticket", json={"input": {"id": 1, "name": new_name}})
print(f"Changed to: {new_name}")
