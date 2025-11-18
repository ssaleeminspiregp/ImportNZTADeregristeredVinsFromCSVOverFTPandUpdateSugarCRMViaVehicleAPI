import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


EXPECTED_HEADERS = [
    "VEHICLE_MAKE",
    "VEHICLE_MODEL",
    "VIN",
    "DEREG_DATE",
    "REGNO",
]


class HeaderValidationError(Exception):
    def __init__(self, actual: list[str] | None) -> None:
        self.actual = actual or []
        message = (
            "Invalid CSV header. "
            f"Expected {EXPECTED_HEADERS}, received {self.actual}"
        )
        super().__init__(message)


@dataclass
class VehicleRecord:
    make: str
    model: str
    vin: str
    dereg_date: str
    rego: str


class CsvProcessor:
    def __init__(self, allowed_makes: Iterable[str]) -> None:
        self.allowed_makes = {item.upper() for item in allowed_makes}

    def load(self, source: Path) -> Iterator[VehicleRecord]:
        with open(source, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self._validate_headers(reader.fieldnames)
            for row in reader:
                make = self._clean_value(row.get("VEHICLE_MAKE")).upper()
                if make not in self.allowed_makes:
                    continue

                vin = self._clean_value(row.get("VIN")).upper()
                if not vin:
                    logging.warning("Skipping record without VIN for make %s", make)
                    continue

                dereg = self._format_date(self._clean_value(row.get("DEREG_DATE")))
                yield VehicleRecord(
                    make=make,
                    model=self._clean_value(row.get("VEHICLE_MODEL")),
                    vin=vin,
                    dereg_date=dereg,
                    rego=self._clean_value(row.get("REGNO")).upper(),
                )

    @staticmethod
    def _format_date(raw: str | None) -> str:
        if not raw:
            return ""
        raw = raw.strip()
        if len(raw) == 8 and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
        try:
            return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
        except ValueError:
            logging.warning("Unrecognized date %s; omitting value", raw)
            return ""

    @staticmethod
    def _validate_headers(headers: list[str] | None) -> None:
        if headers is None:
            raise HeaderValidationError(headers)
        cleaned = []
        for idx, h in enumerate(headers):
            if idx == 0 and h.startswith("\ufeff"):
                h = h.replace("\ufeff", "", 1)
            cleaned.append(h.strip())
        if cleaned != EXPECTED_HEADERS:
            raise HeaderValidationError(cleaned)

    @staticmethod
    def _clean_value(value: str | None) -> str:
        if value is None:
            return ""
        return value.replace("\ufeff", "", 1).strip()
