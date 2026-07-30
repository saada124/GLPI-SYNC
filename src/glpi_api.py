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
        self._search_option_cache: dict[str, dict] = {}

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
        if itemtype not in self._search_option_cache:
            self._search_option_cache[itemtype] = self._request("GET", f"listSearchOptions/{itemtype}")
        opts = self._search_option_cache[itemtype]
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

    def _search_paginated(
        self,
        itemtype: str,
        forcedisplay: list[str] | None = None,
        criteria: list[dict] | None = None,
    ) -> list[dict]:
        """Paginate through *all* search results. Returns field-ID-keyed dicts."""
        all_rows: list[dict] = []
        body: dict = {"start": 0, "limit": 100, "is_deleted": 0}
        if forcedisplay:
            body["forcedisplay"] = forcedisplay
        if criteria:
            body["criteria"] = criteria

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

    def _resolve_search_field_id(self, itemtype: str, *candidate_names: str) -> int | None:
        """Resolve a search-option field ID by one of several possible display names."""
        for name in candidate_names:
            field_id = self._find_field_id_by_name(itemtype, name)
            if field_id is not None:
                return field_id
        return None

    def get_ticket_user_index(
        self, ticket_ids: set[int] | list[int] | None = None
    ) -> dict[tuple[int, int], dict]:
        ticket_field = self._resolve_search_field_id(
            "Ticket_User", "Tickets", "Ticket", "tickets_id"
        )
        user_field = self._resolve_search_field_id(
            "Ticket_User", "Users", "User", "users_id"
        )
        type_field = self._resolve_search_field_id(
            "Ticket_User", "Type", "type"
        )
        date_mod_field = self._resolve_search_field_id(
            "Ticket_User", "Last update", "date_mod"
        )

        forcedisplay = ["2"]  # id
        for fid in (ticket_field, user_field, type_field, date_mod_field):
            if fid is not None:
                forcedisplay.append(str(fid))

        # De-dupe while preserving order
        seen: set[str] = set()
        forcedisplay = [x for x in forcedisplay if not (x in seen or seen.add(x))]

        criteria = None
        if ticket_ids is not None and not ticket_ids:
            # Explicitly "no tickets requested" — return empty rather than
            # silently doing a full scan, which would defeat scoping for any
            # future caller that passes an empty collection intentionally.
            return {}
        ids = sorted({int(t) for t in ticket_ids}) if ticket_ids else []
        if ids:
            if ticket_field is None:
                # Can't filter server-side without the field ID — fetching the
                # full table here would silently defeat the point of passing
                # ticket_ids, so surface that instead of pretending it worked.
                raise RuntimeError(
                    "get_ticket_user_index: ticket_ids filter requested but "
                    "the Ticket_User ticket-link search field could not be "
                    "resolved; refusing to fall back to a full-table scan."
                )
            criteria = []
            for i, tid in enumerate(ids):
                clause = {"field": ticket_field, "searchtype": "equals", "value": tid}
                if i > 0:
                    clause["link"] = "OR"
                criteria.append(clause)

        rows = self._search_paginated(
            "Ticket_User", forcedisplay=forcedisplay, criteria=criteria
        )
        index: dict[tuple[int, int], dict] = {}

        for row in rows:
            glpi_id = row.get("2")
            if not glpi_id:
                continue

            tickets_id = row.get(str(ticket_field)) if ticket_field is not None else None
            users_id = row.get(str(user_field)) if user_field is not None else None
            if tickets_id in (None, "") or users_id in (None, ""):
                # Fallback: one GET only when search options couldn't map fields
                try:
                    item = self.get_item("Ticket_User", int(glpi_id))
                    tickets_id = item.get("tickets_id")
                    users_id = item.get("users_id")
                    type_val = item.get("type")
                    date_mod_val = item.get("date_mod")
                except Exception:
                    continue
            else:
                type_val = row.get(str(type_field)) if type_field is not None else None
                date_mod_val = (
                    row.get(str(date_mod_field)) if date_mod_field is not None else None
                )
                item = {
                    "id": int(glpi_id),
                    "tickets_id": int(tickets_id),
                    "users_id": int(users_id),
                }
                if type_val not in (None, ""):
                    try:
                        item["type"] = int(type_val)
                    except (TypeError, ValueError):
                        item["type"] = type_val
                if date_mod_val not in (None, ""):
                    item["date_mod"] = date_mod_val

            try:
                key = (int(tickets_id), int(users_id))
            except (TypeError, ValueError):
                continue

            # Keep first match; GLPI uniqueness is per ticket+user(+type)
            if key not in index:
                if "id" not in item:
                    item["id"] = int(glpi_id)
                if date_mod_val not in (None, "") and "date_mod" not in item:
                    item["date_mod"] = date_mod_val
                index[key] = item

        return index

    def find_ticket_user(
        self,
        tickets_id: int,
        users_id: int,
        type_value: int | None = None,
    ) -> dict | None:
        """Find one Ticket_User by tickets_id + users_id (+ optional type)."""
        ticket_field = self._resolve_search_field_id(
            "Ticket_User", "Tickets", "Ticket", "tickets_id"
        )
        user_field = self._resolve_search_field_id(
            "Ticket_User", "Users", "User", "users_id"
        )
        type_field = self._resolve_search_field_id(
            "Ticket_User", "Type", "type"
        )
        if ticket_field is None or user_field is None:
            return None

        criteria = [
            {"link": "AND", "field": ticket_field, "searchtype": "equals", "value": tickets_id},
            {"link": "AND", "field": user_field, "searchtype": "equals", "value": users_id},
        ]
        if type_value is not None and type_field is not None:
            criteria.append(
                {"link": "AND", "field": type_field, "searchtype": "equals", "value": type_value}
            )

        forcedisplay = ["2", str(ticket_field), str(user_field)]
        if type_field is not None:
            forcedisplay.append(str(type_field))

        rows = self._search_paginated(
            "Ticket_User", forcedisplay=forcedisplay, criteria=criteria
        )
        if not rows:
            return None

        row = rows[0]
        glpi_id = row.get("2")
        if not glpi_id:
            return None

        item = {
            "id": int(glpi_id),
            "tickets_id": int(tickets_id),
            "users_id": int(users_id),
        }
        if type_field is not None:
            tv = row.get(str(type_field))
            if tv not in (None, ""):
                try:
                    item["type"] = int(tv)
                except (TypeError, ValueError):
                    item["type"] = tv
        elif type_value is not None:
            item["type"] = type_value
        return item

    def get_all_ticket_users(self) -> list[dict]:
        """Get ALL Ticket_User records as name-keyed dicts.

        Prefer the bulk search index (no N+1). Falls back to per-id GET only
        if the index cannot resolve ticket/user fields.
        """
        index = self.get_ticket_user_index()
        if index:
            return list(index.values())

        # Last-resort fallback: old N+1 path
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

    def get_ticket_actors(
        self, index: dict[tuple[int, int], dict] | None = None
    ) -> dict[int, dict[str, int]]:
        """Map tickets_id -> {requester: users_id, assignee: users_id, ...}.

        GLPI types: 1=Requester, 2=Assigned, 3=Observer.
        """
        if index is None:
            index = self.get_ticket_user_index()
        actors: dict[int, dict[str, int]] = {}
        for (tickets_id, users_id), item in (index or {}).items():
            try:
                tid = int(tickets_id)
                uid = int(users_id)
            except (TypeError, ValueError):
                continue
            slot = actors.setdefault(tid, {})
            try:
                t = int(item.get("type")) if item.get("type") not in (None, "") else None
            except (TypeError, ValueError):
                t = None
            if t == 1:
                slot.setdefault("requester", uid)
            elif t == 2:
                slot.setdefault("assignee", uid)
            elif t == 3:
                slot.setdefault("observer", uid)
            else:
                # Unknown type — keep first seen as fallback requester
                slot.setdefault("requester", uid)
        return actors

    def update_item(self, itemtype: str, item_id: int, fields: dict) -> bool:
        fields["id"] = item_id
        self._request("PUT", itemtype, json={"input": fields})
        return True

    def delete_item(self, itemtype: str, item_id: int) -> bool:
        self._request("DELETE", f"{itemtype}/{item_id}")
        return True