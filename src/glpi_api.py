import requests
from typing import Any
from time import sleep

class GLPIAPI:
    def __init__(self, url: str, app_token: str, user_token: str):
        self.base_url = url.rstrip("/") + "/"
        self.app_token = app_token
        self.user_token = user_token
        self.session_token: str | None = None
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "App-Token": self.app_token,
        })

    def _get_headers(self) -> dict:
        headers = {"App-Token": self.app_token}
        if self.session_token:
            headers["Session-Token"] = self.session_token
        return headers

    def init_session(self) -> str:
        resp = self._session.post(
            self.base_url + "initSession",
            headers=self._get_headers(),
            json={"user_token": self.user_token},
        )
        resp.raise_for_status()
        self.session_token = resp.json().get("session_token")
        return self.session_token

    def kill_session(self) -> None:
        if not self.session_token:
            return
        try:
            self._session.post(
                self.base_url + "killSession",
                headers=self._get_headers(),
            )
        except requests.RequestException:
            pass
        finally:
            self.session_token = None

    def __enter__(self):
        self.init_session()
        return self

    def __exit__(self, *args):
        self.kill_session()

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = self.base_url + endpoint.lstrip("/")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = self._session.request(method, url, headers=self._get_headers(), timeout=60, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError:
                if attempt == max_retries - 1:
                    body = resp.text[:500]
                    raise requests.HTTPError(f"{resp.status_code} {resp.reason} for {endpoint}: {body}")
                sleep(2 ** attempt)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == max_retries - 1:
                    raise
                sleep(2 ** attempt)

    def get_item(self, itemtype: str, item_id: int) -> dict:
        return self._request("GET", f"{itemtype}/{item_id}")

    def search(self, itemtype: str, criteria: list[dict] | None = None) -> list[dict]:
        body: dict = {"start": 0, "limit": 9999, "is_deleted": 0}
        if criteria:
            body["criteria"] = criteria
        data = self._request("POST", f"search/{itemtype}", json=body)
        raw = data.get("data", [])
        if isinstance(raw, dict):
            return list(raw.values())
        return raw

    def _find_field_id_by_name(self, itemtype: str, name: str) -> int | None:
        """Return the search-option field ID whose name matches `name`."""
        opts = self._request("GET", f"listSearchOptions/{itemtype}")
        for key, val in opts.items():
            if isinstance(val, dict) and val.get("name") == name:
                return int(key)
        return None

    def _get_date_mod_field_id(self, itemtype: str) -> int | None:
        """Find the search-option field ID for `date_mod` (Last update)."""
        return self._find_field_id_by_name(itemtype, "Last update")

    def get_changed_items(self, itemtype: str, since_timestamp: str) -> list[dict]:
        """Fetch items modified since `since_timestamp`.

        Uses the search API with a date_mod> filter to find changed IDs, then
        fetches each full record via GET /itemtype/{id}.  Returns name-keyed
        dicts (same format as get_item / get_all).

        Falls back to get_all() if the itemtype lacks a date_mod search field.
        """
        date_mod_field_id = self._get_date_mod_field_id(itemtype)
        if date_mod_field_id is None:
            # No date_mod search field available; fall back to full fetch + client filter
            return self.get_all(itemtype)

        items: list[dict] = []
        criteria = [{"field": date_mod_field_id, "searchtype": "morethan", "value": since_timestamp}]
        body: dict = {
            "start": 0, "limit": 100, "is_deleted": 0,
            "criteria": criteria,
            "forcedisplay": ["2"],
        }

        while True:
            data = self._request("POST", f"search/{itemtype}", json=body)
            rows = data.get("data", [])
            total = data.get("totalcount", 0)
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not rows:
                break

            for row in rows:
                glpi_id = row.get("2")
                if not glpi_id:
                    continue
                try:
                    item = self.get_item(itemtype, int(glpi_id))
                    items.append(item)
                except Exception:
                    continue

            body["start"] += len(rows)
            if body["start"] >= total:
                break

        return items

    def get_all(self, itemtype: str) -> list[dict]:
        """Get all items of a reference type using paginated requests."""
        url = self.base_url + itemtype
        headers = self._get_headers()
        max_retries = 3
        all_items = []
        range_start = 0
        page_size = 500
        while True:
            retries = 0
            while retries < max_retries:
                try:
                    resp = self._session.get(
                        url,
                        headers={**headers, "Range": f"items={range_start}-{range_start + page_size - 1}"},
                        timeout=60,
                    )
                    resp.raise_for_status()
                    chunk = resp.json()
                    break
                except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
                    if retries == max_retries - 1:
                        if all_items:
                            return all_items  # return what we have
                        raise
                    sleep(2 ** retries)
                    retries += 1
            if not chunk:
                break
            all_items.extend(chunk)
            if len(chunk) < page_size:
                break
            range_start += page_size
        return all_items

    def add_item(self, itemtype: str, fields: dict) -> int:
        result = self._request("POST", itemtype, json={"input": fields})
        if isinstance(result, int):
            return result
        if isinstance(result, list) and len(result) > 0:
            return result[0].get("id", result[0])
        return result.get("id", 0)

    def _search_paginated(self, itemtype: str, forcedisplay: list[str] | None = None) -> list[dict]:
        """Paginate through *all* search results. Returns field-ID-keyed dicts."""
        all_rows: list[dict] = []
        body: dict = {"start": 0, "limit": 100, "is_deleted": 0}
        if forcedisplay:
            body["forcedisplay"] = forcedisplay

        while True:
            data = self._request("POST", f"search/{itemtype}", json=body)
            rows = data.get("data", [])
            total = data.get("totalcount", 0)
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not rows:
                break
            all_rows.extend(rows)
            body["start"] += len(rows)
            if body["start"] >= total:
                break
        return all_rows

    def get_all_ticket_users(self) -> list[dict]:
        """Get ALL Ticket_User records as name-keyed dicts.

        The list endpoint is capped at 15, and search returns field-ID keys.
        This paginates search for IDs, then fetches each full record via get_item.
        """
        rows = self._search_paginated("Ticket_User", forcedisplay=["2"])
        items: list[dict] = []
        for row in rows:
            glpi_id = row.get("2")
            if not glpi_id:
                continue
            try:
                item = self.get_item("Ticket_User", int(glpi_id))
                items.append(item)
            except Exception:
                continue
        return items

    def update_item(self, itemtype: str, item_id: int, fields: dict) -> bool:
        fields["id"] = item_id
        self._request("PUT", itemtype, json={"input": fields})
        return True
