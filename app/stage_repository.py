import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from app.csv_processor import VehicleRecord


@dataclass
class StagedEntry:
    stage_id: str
    record: VehicleRecord


class StageRepository:
    def __init__(
        self,
        dataset: str,
        table: str,
        location: str,
        client: Optional[bigquery.Client] = None,
    ) -> None:
        self.dataset = dataset
        self.table = table
        self.location = location
        self.client = client or bigquery.Client()
        self.project = self.client.project
        self._table_id = f"{self.project}.{self.dataset}.{self.table}"

    def ensure_resources(self) -> None:
        dataset_ref = bigquery.DatasetReference(self.project, self.dataset)
        try:
            self.client.get_dataset(dataset_ref)
        except NotFound:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = self.location
            self.client.create_dataset(dataset)
            logging.info(
                "Created BigQuery dataset %s.%s", self.project, self.dataset
            )

        table_ref = dataset_ref.table(self.table)
        try:
            self.client.get_table(table_ref)
        except NotFound:
            schema = [
                bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("vin", "STRING"),
                bigquery.SchemaField("vehicle_make", "STRING"),
                bigquery.SchemaField("vehicle_model", "STRING"),
                bigquery.SchemaField("dereg_date", "STRING"),
                bigquery.SchemaField("reg_plate", "STRING"),
                bigquery.SchemaField("status", "STRING"),
                bigquery.SchemaField("date_created", "TIMESTAMP"),
                bigquery.SchemaField("date_modified", "TIMESTAMP"),
                bigquery.SchemaField("gcs_uri", "STRING"),
                bigquery.SchemaField("error_message", "STRING"),
            ]
            table = bigquery.Table(table_ref, schema=schema)
            self.client.create_table(table)
            logging.info("Created BigQuery table %s", self._table_id)

    def stage_records(
        self, records: Iterable[VehicleRecord], gcs_uri: str
    ) -> List[StagedEntry]:
        staged: List[StagedEntry] = []
        rows = []
        now = datetime.now(timezone.utc).isoformat()

        for record in records:
            stage_id = str(uuid.uuid4())
            staged.append(StagedEntry(stage_id=stage_id, record=record))
            rows.append(
                {
                    "id": stage_id,
                    "vin": record.vin,
                    "vehicle_make": record.make,
                    "vehicle_model": record.model,
                    "dereg_date": record.dereg_date,
                    "reg_plate": record.rego,
                    "status": "pending",
                    "date_created": now,
                    "date_modified": now,
                    "gcs_uri": gcs_uri,
                    "error_message": None,
                }
            )

        if rows:
            errors = self.client.insert_rows_json(self._table_id, rows)
            if errors:
                raise RuntimeError(f"Failed to insert staged rows: {errors}")

        return staged

    def mark_pushed(self, stage_id: str) -> None:
        self._update_status(stage_id, "pushed", None)

    def record_error(self, stage_id: str, error_message: str) -> None:
        self._update_status(stage_id, "pending", error_message)

    def _update_status(
        self, stage_id: str, status: str, error_message: Optional[str]
    ) -> None:
        query = f"""
        UPDATE `{self._table_id}`
        SET status = @status,
            error_message = @error,
            date_modified = @modified
        WHERE id = @id
        """
        self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("status", "STRING", status),
                    bigquery.ScalarQueryParameter("error", "STRING", error_message),
                    bigquery.ScalarQueryParameter(
                        "modified",
                        "TIMESTAMP",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                    bigquery.ScalarQueryParameter("id", "STRING", stage_id),
                ]
            ),
        ).result()
