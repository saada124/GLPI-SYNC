import requests
from typing import Any

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
        resp = self._session.request(method, url, headers=self._get_headers(), **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_item(self, itemtype: str, item_id: int) -> dict:
        return self._request("GET", f"{itemtype}/{item_id}")

    def search(self, itemtype: str, criteria: list[dict] | None = None) -> list[dict]:
        params = {"start": 0, "limit": 9999, "is_deleted": 0}
        if criteria:
            for i, criterion in enumerate(criteria):
                for key, value in criterion.items():
                    params[f"criteria[{i}][{key}]"] = value
        data = self._request("GET", f"search/{itemtype}", params=params)
        return data.get("data", [])

    def add_item(self, itemtype: str, fields: dict) -> int:
        result = self._request("POST", itemtype, json={"input": fields})
        if isinstance(result, int):
            return result
        if isinstance(result, list) and len(result) > 0:
            return result[0].get("id", result[0])
        return result.get("id", 0)

    def update_item(self, itemtype: str, item_id: int, fields: dict) -> bool:
        fields["id"] = item_id
        result = self._request("PUT", itemtype, json={"input": fields})
        return True

    def get_profiles(self) -> list[dict]:
        data = self._request("GET", "search/Profile")
        return data.get("data", [])
