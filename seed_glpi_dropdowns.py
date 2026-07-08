"""
GLPI Dropdown Seeding Script (to help with the large dropdowns/inserts of glpi)
============================
This script logs into a GLPI 10.0.26 instance using REST API (App Token + User Token),
creates Suppliers, ITIL Categories, and Computer Types if they don't exist, and logs out.

Usage:
    - Edit the constants GLPI_URL, APP_TOKEN, USER_TOKEN below.
    - Run `pip install requests` if not already installed.
    - Execute the script: `python seed_glpi_dropdowns.py`.

Features:
    - Idempotent: skips items that already exist.
    - Error handling with retries and clear logging of actions.
    - Disables SSL verification by default (configurable).

You can edit whatever you want in the lists so it can match your needs.
"""

import requests
import time
import logging
from typing import List

# === Configuration ===
GLPI_URL = "http://localhost/glpi/apirest.php" 
APP_TOKEN = "v3Hf4GN4xnk7JN9zXLfEFYbQ8ABnlXqnAxjEsWs4"
USER_TOKEN = "aZJJpf8A8FKjvO0hqikf150YS1Op9Np6owX6iECu"

# Option: Set verify_ssl=False to skip certificate verification (e.g. self-signed cert)
VERIFY_SSL = True

# Items to seed
SUPPLIERS = [
    "VILAVI", "REDGO", "AZIZA", "APPOG", "BRASLAM",
    "DGC", "GDI", "DGN", "ORKIDIS"
]
ITIL_CATEGORIES = [
    "Aloha Manager", "Assabil", "Assabil Asset", "Assistance", "Caisse",
    "Camera de surveillance", "Contrôle d'accès", "Coswin", "Declaration Employeur",
    "GLOBALNET", "Google Workspace", "GPS", "Imprimante", "Interne", "Intervention",
    "Maintenance PC", "Maintenance Serveur", "NEXTCLOUD", "OOREDOO", "Order-Scan",
    "Partage et Accès", "Pointeuse", "Qlik view", "Requête", "Réseaux",
    "Sage X3", "SAP SuccessFactors", "Smartphone", "Swibtime", "SXA",
    "TELECOM", "VPN", "Windows", "XRT"
]
COMPUTER_TYPES = [
    "Écran", "Laptop", "Desktop", "Téléphone", "Imprimante", "Autre"
]

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# === GLPI API Functions ===
def glpi_request(method: str, endpoint: str, headers=None, params=None, json=None, retry=3):
    """
    Helper to call GLPI API with retries on failure.
    """
    url = f"{GLPI_URL}/{endpoint}"
    for attempt in range(1, retry+1):
        try:
            response = requests.request(
                method, url,
                headers=headers, params=params, json=json,
                verify=VERIFY_SSL
            )
        except requests.RequestException as e:
            logger.warning(f"Request exception (attempt {attempt}): {e}")
            if attempt == retry:
                raise
            time.sleep(1)
            continue

        if response.status_code >= 500:
            logger.warning(f"Server error (status {response.status_code}, attempt {attempt})")
            if attempt == retry:
                raise Exception(f"API server error: {response.status_code}")
            time.sleep(1)
            continue
        return response
    return None

def login():
    """
    Authenticate with GLPI using User Token and App Token. Returns session token.
    """
    headers = {
        "Authorization": f"user_token {USER_TOKEN}",
        "App-Token": APP_TOKEN,
        "Content-Type": "application/json"
    }
    response = glpi_request("GET", "initSession?get_full_session=true", headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to initSession: {response.status_code} - {response.text}")
    data = response.json()
    session_token = data.get("session_token")
    if not session_token:
        raise Exception("No session_token in response.")
    logger.info("Logged into GLPI, obtained session token.")
    return session_token

def logout(session_token: str):
    """
    Terminate the GLPI session.
    """
    headers = {"Session-Token": session_token, "App-Token": APP_TOKEN}
    response = glpi_request("GET", "killSession", headers=headers)
    if response.status_code == 200:
        logger.info("Logged out of GLPI.")
    else:
        logger.warning(f"Failed to killSession: {response.status_code}")

def list_items(session_token: str, item_type: str) -> List[dict]:
    """
    List all items of a given type. Returns a list of dicts with item data.
    """
    headers = {"Session-Token": session_token, "App-Token": APP_TOKEN}
    # Use a large range to retrieve all items
    params = {"range": "0-1000"}
    response = glpi_request("GET", item_type, headers=headers, params=params)
    if response.status_code != 200:
        raise Exception(f"Failed to list {item_type}: {response.status_code} - {response.text}")
    return response.json()

def create_item(session_token: str, item_type: str, name: str):
    """
    Create an item of type item_type with the given name.
    """
    headers = {"Session-Token": session_token, "App-Token": APP_TOKEN, "Content-Type": "application/json"}
    payload = {"input": {"name": name}}
    response = glpi_request("POST", item_type, headers=headers, json=payload)
    if response.status_code == 201:
        item_id = response.json().get("id")
        logger.info(f"Created {item_type} '{name}' (ID: {item_id}).")
        return item_id
    else:
        # If already exists or other error
        raise Exception(f"Failed to create {item_type} '{name}': {response.status_code} - {response.text}")

def seed_items(session_token: str, item_type: str, names: List[str]):
    """
    Ensure each name in names exists as an item of the given type.
    """
    existing = list_items(session_token, item_type)
    existing_names = {item.get("name") for item in existing}
    logger.info(f"Found {len(existing)} existing {item_type}(s).")
    for name in names:
        if name in existing_names:
            logger.info(f"Skip {item_type} '{name}': already exists.")
        else:
            logger.info(f"Creating {item_type} '{name}'...")
            create_item(session_token, item_type, name)

def main():
    # Login
    try:
        session_token = login()
    except Exception as e:
        logger.error(e)
        return

    try:
        # Seed suppliers
        seed_items(session_token, "Supplier", SUPPLIERS)
        # Seed ITIL categories
        seed_items(session_token, "ITILCategory", ITIL_CATEGORIES)
        # Seed computer types
        seed_items(session_token, "ComputerType", COMPUTER_TYPES)
    except Exception as e:
        logger.error(e)
    # finally:
        # Logout
        # logout(session_token)

if __name__ == "__main__":
    main()
