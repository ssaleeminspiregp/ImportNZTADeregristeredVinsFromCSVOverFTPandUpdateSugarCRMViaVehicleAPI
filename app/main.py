import base64
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, request

from app.config import AppConfig
from app.csv_processor import CsvProcessor, HeaderValidationError, EXPECTED_HEADERS
from app.ftp_client import FtpDownloader
from app.notifier import EmailNotifier, build_notifier
from app.stage_repository import StageRepository
from app.storage_writer import StorageWriter
from app.sugar_client import SugarCrmClient

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def entrypoint():
    if request.method == "GET":
        return jsonify({"status": "ready"})

    event_payload = _decode_pubsub(request.get_json(silent=True))
    summary = execute_pipeline(event_payload)
    return jsonify(summary)


def execute_pipeline(trigger_payload: Dict[str, Any]) -> Dict[str, Any]:
    config = AppConfig.from_env()
    notifier = build_notifier(config.email)
    ftp = FtpDownloader(
        host=config.ftp_host,
        port=config.ftp_port,
        username=config.ftp_username,
        password=config.ftp_password,
        timeout=config.ftp_timeout,
        block_size=config.ftp_block_size,
    )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temp_file:
        temp_path = Path(temp_file.name)
    try:
        ftp.download(config.ftp_remote_path, temp_path)
        storage_writer = StorageWriter(config.gcs_bucket, config.gcs_prefix)
        gcs_uri = storage_writer.upload(temp_path)

        processor = CsvProcessor(config.allowed_makes)
        try:
            records = list(processor.load(temp_path))
        except HeaderValidationError as exc:
            _handle_header_error(notifier, temp_path, exc)
            raise
        logging.info("Loaded %s eligible VIN records", len(records))

        stage_repo = StageRepository(
            dataset=config.bq_dataset,
            table=config.bq_table,
            location=config.bq_location,
        )
        stage_repo.ensure_resources()
        staged_entries = stage_repo.stage_records(records, gcs_uri)

        sugar = SugarCrmClient(
            base_url=config.sugar_base_url,
            username=config.sugar_username,
            password=config.sugar_password,
            client_id=config.sugar_client_id,
            client_secret=config.sugar_client_secret,
            platform=config.sugar_platform,
            grant_type=config.sugar_grant_type,
            timeout=config.sugar_timeout,
        )
        sugar.authenticate()

        successes = 0
        failures: List[Dict[str, str]] = []
        for entry in staged_entries:
            record = entry.record
            try:
                sugar.create_or_update_vehicle(record, team_id=None)
                stage_repo.mark_pushed(entry.stage_id)
                successes += 1
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to sync VIN %s", record.vin)
                stage_repo.record_error(entry.stage_id, str(exc))
                failures.append({"vin": record.vin, "error": str(exc)})

        return {
            "gcs_uri": gcs_uri,
            "processed_records": len(records),
            "successful_updates": successes,
            "failed_updates": len(failures),
            "failures": failures,
            "trigger_payload": trigger_payload,
        }
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _decode_pubsub(body: Dict[str, Any] | None) -> Dict[str, Any]:
    if not body or "message" not in body:
        return body or {}
    data = body["message"].get("data")
    if not data:
        return {}
    decoded = base64.b64decode(data).decode("utf-8")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return {"raw": decoded}


def _handle_header_error(
    notifier: EmailNotifier | None, source_path: Path, error: HeaderValidationError
) -> None:
    message = (
        "NZTA deregistered VIN ingestion failed due to an invalid CSV header.\n\n"
        f"File: {source_path}\n"
        f"Expected: {', '.join(EXPECTED_HEADERS)}\n"
        f"Received: {', '.join(error.actual) if error.actual else 'None'}\n"
    )
    logging.error(message)
    if not notifier:
        logging.error(
            "Unable to send notification email; SMTP settings not configured"
        )
        return
    try:
        notifier.send(
            subject="NZTA Deregistered VIN ingest failed - header validation",
            body=message,
        )
    except Exception:
        logging.exception("Failed to send header validation alert email")
