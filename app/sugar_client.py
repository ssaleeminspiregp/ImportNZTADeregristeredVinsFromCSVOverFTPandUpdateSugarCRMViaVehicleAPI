import logging
from typing import Optional
from urllib.parse import urljoin

import requests

from app.csv_processor import VehicleRecord


class SugarCrmClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client_id: str,
        client_secret: str,
        platform: str,
        grant_type: str,
        timeout: int,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.client_id = client_id
        self.client_secret = client_secret
        self.platform = platform
        self.grant_type = grant_type
        self.timeout = timeout
        self.session = requests.Session()
        self._access_token: Optional[str] = None

    def authenticate(self) -> None:
        token_url = urljoin(self.base_url, "rest/v11_6/oauth2/token")
        payload = {
            "grant_type": self.grant_type or "password",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": self.username,
            "password": self.password,
            "platform": self.platform,
        }
        response = self.session.post(token_url, data=payload, timeout=self.timeout)
        response.raise_for_status()
        self._access_token = response.json()["access_token"]
        logging.info("Authenticated to SugarCRM as %s", self.username)

    def find_vehicle_id(self, vin: str) -> Optional[str]:
        if not self._access_token:
            raise RuntimeError("SugarCRM client not authenticated")
        url = urljoin(self.base_url, "rest/v11_20/VHE_Vehicle")
        params = {
            "filter[0][vin_c][$equals]": vin,
            "max_num": 1,
        }
        response = self._request("get", url, params=params)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("records") or []
        if not records:
            return None
        return records[0].get("id")

    def update_vehicle(self, vehicle_id: str, record: VehicleRecord) -> None:
        if not self._access_token:
            raise RuntimeError("SugarCRM client not authenticated")
        url = urljoin(self.base_url, f"rest/v11_20/VHE_Vehicle/{vehicle_id}")
        payload = {
            "vehicle_status_c": "Deregistered",
            "latest_dereg_date_c": record.dereg_date,
        }
        response = self._request("put", url, params=payload)
        response.raise_for_status()

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise RuntimeError("SugarCRM client not authenticated")
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        headers = kwargs.pop("headers", {})
        headers.update(self._auth_headers())
        kwargs["headers"] = headers

        response = self.session.request(method=method, url=url, **kwargs)
        if response.status_code == 401:
            logging.warning("SugarCRM request unauthorized; refreshing token and retrying")
            self.authenticate()
            headers.update(self._auth_headers())
            kwargs["headers"] = headers
            response = self.session.request(method=method, url=url, **kwargs)
        return response
