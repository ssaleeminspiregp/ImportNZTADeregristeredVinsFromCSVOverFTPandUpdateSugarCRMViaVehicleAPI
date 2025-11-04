import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


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
            for row in reader:
                make = (row.get("VEHICLE_MAKE") or "").strip().upper()
                if make not in self.allowed_makes:
                    continue

                vin = (row.get("VIN") or "").strip().upper()
                if not vin:
                    logging.warning("Skipping record without VIN for make %s", make)
                    continue

                dereg = self._format_date(row.get("DEREG_DATE"))
                yield VehicleRecord(
                    make=make,
                    model=(row.get("VEHICLE_MODEL") or "").strip(),
                    vin=vin,
                    dereg_date=dereg,
                    rego=(row.get("REGNO") or "").strip().upper(),
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
            logging.warning("Unrecognized date %s; passing through", raw)
            return raw
