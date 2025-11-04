import logging
from typing import Optional
from urllib.parse import urljoin

import requests

from app.csv_processor import VehicleRecord


class SugarCrmClient:
    def __init__(self, base_url: str, username: str, password: str,
                 client_id: str, client_secret: str, platform: str, timeout: int) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.client_id = client_id
        self.client_secret = client_secret
        self.platform = platform
        self.timeout = timeout
        self.session = requests.Session()
        self._access_token: Optional[str] = None

    def authenticate(self) -> None:
        token_url = urljoin(self.base_url, "rest/v11_6/oauth2/token")
        payload = {
            "grant_type": "password",
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

    def create_or_update_vehicle(self, record: VehicleRecord, team_id: Optional[str] = None) -> None:
        if not self._access_token:
            raise RuntimeError("SugarCRM client not authenticated")

        url = urljoin(self.base_url, "rest/v11_20/VHE_Vehicle/createUpdate")
        payload = {
            "idField": "vin_c",
            "idValue": record.vin,
            "vin_c": record.vin,
            "vehicle_status_c": "Deregistered",
            "latest_dereg_date_c": record.dereg_date,
            "reg_plate_c": record.rego,
            "vehicle_make_c": record.make,
            "vehicle_model_c": record.model,
        }
        if team_id:
            payload["team_id"] = team_id

        headers = {"Authorization": f"Bearer {self._access_token}"}
        response = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
