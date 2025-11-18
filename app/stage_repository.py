import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from google.api_core.exceptions import BadRequest, NotFound
from google.cloud import bigquery

from app.csv_processor import VehicleRecord


@dataclass
class StagedEntry:
    stage_id: str
    record: VehicleRecord
    source_filename: Optional[str] = None


class StageRepository:
    STREAMING_BUFFER_RETRIES = 12
    STREAMING_BUFFER_DELAY_SECONDS = 15

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
        self.client = client or bigquery.Client(location=location)
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
                bigquery.SchemaField("dereg_date", "DATE"),
                bigquery.SchemaField("reg_plate", "STRING"),
                bigquery.SchemaField("status", "STRING"),
                bigquery.SchemaField("date_created", "TIMESTAMP"),
                bigquery.SchemaField("date_modified", "TIMESTAMP"),
                bigquery.SchemaField("source_filename", "STRING"),
                bigquery.SchemaField("error_message", "STRING"),
            ]
            table = bigquery.Table(table_ref, schema=schema)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="dereg_date",
            )
            table.clustering_fields = ["vin"]
            self.client.create_table(table)
            logging.info("Created BigQuery table %s", self._table_id)

    def stage_records(
        self, records: Iterable[VehicleRecord], source_filename: str
    ) -> List[StagedEntry]:
        staged: List[StagedEntry] = []
        rows = []
        now = datetime.now(timezone.utc).isoformat()

        for record in records:
            stage_id = str(uuid.uuid4())
            staged.append(
                StagedEntry(
                    stage_id=stage_id,
                    record=record,
                    source_filename=source_filename,
                )
            )
            rows.append(
                {
                    "id": stage_id,
                    "vin": record.vin,
                    "vehicle_make": record.make,
                    "vehicle_model": record.model,
                    "dereg_date": record.dereg_date or None,
                    "reg_plate": record.rego,
                    "status": "pending",
                    "date_created": now,
                    "date_modified": now,
                    "source_filename": source_filename,
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
        self._update_status(stage_id, "failed", error_message)

    def mark_failed_by_file(self, source_filename: str, error_message: str) -> bool:
        """Mark every pending row tied to a staged file as failed."""
        query = f"""
        UPDATE `{self._table_id}`
        SET status = 'failed',
            error_message = @error,
            date_modified = @modified
        WHERE source_filename = @file_name AND status = 'pending'
        """
        return self._run_update_query(
            query,
            [
                bigquery.ScalarQueryParameter("error", "STRING", error_message),
                bigquery.ScalarQueryParameter(
                    "modified",
                    "TIMESTAMP",
                    datetime.now(timezone.utc).isoformat(),
                ),
                bigquery.ScalarQueryParameter("file_name", "STRING", source_filename),
            ],
        )

    def fetch_by_status(self, status: str = "pending", min_age_minutes: int = 0) -> list[StagedEntry]:
        query = f"""
        SELECT id, vin, vehicle_make, vehicle_model, dereg_date, reg_plate, source_filename
        FROM `{self._table_id}`
        WHERE status = @status
        AND (@min_age = 0 OR date_created < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @min_age MINUTE))
        """
        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("status", "STRING", status),
                    bigquery.ScalarQueryParameter("min_age", "INT64", min_age_minutes),
                ]
            ),
        )
        records: list[StagedEntry] = []
        for row in job.result():
            vehicle = VehicleRecord(
                make=row["vehicle_make"] or "",
                model=row["vehicle_model"] or "",
                vin=row["vin"] or "",
                dereg_date=row["dereg_date"] or "",
                rego=row["reg_plate"] or "",
            )
            records.append(
                StagedEntry(
                    stage_id=row["id"],
                    record=vehicle,
                    source_filename=row.get("source_filename"),
                )
            )
        return records

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
        self._run_update_query(
            query,
            [
                bigquery.ScalarQueryParameter("status", "STRING", status),
                bigquery.ScalarQueryParameter("error", "STRING", error_message),
                bigquery.ScalarQueryParameter(
                    "modified",
                    "TIMESTAMP",
                    datetime.now(timezone.utc).isoformat(),
                ),
                bigquery.ScalarQueryParameter("id", "STRING", stage_id),
            ],
        )

    def _run_update_query(
        self, query: str, parameters: List[bigquery.ScalarQueryParameter]
    ) -> bool:
        """Execute a BigQuery mutation, tolerating streaming-buffer limitations."""
        attempts = 0
        while True:
            try:
                self.client.query(
                    query,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=parameters,
                        location=self.location,
                    ),
                ).result()
                return True
            except BadRequest as exc:
                message = getattr(exc, "message", str(exc))
                if (
                    message
                    and "streaming buffer" in message.lower()
                    and attempts < self.STREAMING_BUFFER_RETRIES
                ):
                    attempts += 1
                    logging.warning(
                        "BigQuery mutation blocked by streaming buffer (attempt %s/%s); retrying",
                        attempts,
                        self.STREAMING_BUFFER_RETRIES,
                    )
                    time.sleep(self.STREAMING_BUFFER_DELAY_SECONDS)
                    continue
                raise
