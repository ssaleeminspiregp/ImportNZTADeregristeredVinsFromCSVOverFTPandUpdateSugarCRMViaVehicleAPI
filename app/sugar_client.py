import json
import logging
from datetime import date, datetime
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
        logging.debug(
            "SugarCRM find_vehicle_id response for VIN %s: %s",
            vin,
            self._short_response(response),
        )
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
        target_status = "Deregistered"
        target_date = self._format_date(record.dereg_date)
        payload = {
            "vehicle_status_c": target_status,
            "latest_dereg_date_c": target_date,
        }
        response = self._request("put", url, json=payload)
        logging.debug(
            "SugarCRM update_vehicle response for VIN %s: %s",
            record.vin,
            self._short_response(response),
        )
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:  # noqa: BLE001
            raise RuntimeError("SugarCRM update returned non-JSON payload") from exc

        actual_status = body.get("vehicle_status_c")
        actual_date = body.get("latest_dereg_date_c")
        if actual_status != target_status or actual_date != target_date:
            raise RuntimeError(
                "SugarCRM update did not persist expected values: "
                f"status={actual_status}, date={actual_date}"
            )

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise RuntimeError("SugarCRM client not authenticated")
        return {
            "Authorization": f"Bearer {self._access_token}",
            "OAuth-Token": self._access_token,
        }

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
        headers = kwargs.pop("headers", {})
        headers.update(self._auth_headers())
        kwargs["headers"] = headers

        self._log_request(method, url, kwargs)
        response = self.session.request(method=method, url=url, **kwargs)
        self._log_response(method, url, response)
        if response.status_code == 401:
            logging.warning("SugarCRM request unauthorized; refreshing token and retrying")
            self.authenticate()
            headers.update(self._auth_headers())
            kwargs["headers"] = headers
            self._log_request(method, url, kwargs, retry=True)
            response = self.session.request(method=method, url=url, **kwargs)
            self._log_response(method, url, response, retry=True)
        return response

    def _log_request(
        self, method: str, url: str, kwargs: dict, retry: bool = False
    ) -> None:
        log_kwargs = {}
        for key in ("params", "data", "json"):
            if key in kwargs:
                log_kwargs[key] = self._safe_payload(kwargs[key])
        logging.debug(
            "SugarCRM %s request %s (%s): %s",
            method.upper(),
            url,
            "retry" if retry else "initial",
            log_kwargs or "{}",
        )

    def _log_response(
        self, method: str, url: str, response: requests.Response, retry: bool = False
    ) -> None:
        logging.debug(
            "SugarCRM %s response %s (%s): status=%s body=%s",
            method.upper(),
            url,
            "retry" if retry else "initial",
            response.status_code,
            self._short_response(response),
        )

    def _safe_payload(self, payload: object) -> str:
        try:
            text = json.dumps(payload)
        except TypeError:
            text = str(payload)
        return self._truncate(text)

    def _short_response(self, response: requests.Response) -> str:
        text = response.text
        return self._truncate(text)

    def _truncate(self, text: str, length: int = 500) -> str:
        return text if len(text) <= length else f"{text[:length]}…"

    @staticmethod
    def _format_date(value: Optional[str | date | datetime]) -> Optional[str]:
        if value is None or value == "":
            return None
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)
