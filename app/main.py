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

    storage_writer = StorageWriter(
        bucket=config.gcs_bucket,
        raw_prefix=config.gcs_raw_prefix,
        processed_prefix=config.gcs_processed_prefix,
        error_prefix=config.gcs_error_prefix,
    )
    stage_repo = StageRepository(
        dataset=config.bq_dataset,
        table=config.bq_table,
        location=config.bq_location,
    )
    stage_repo.ensure_resources()

    processed_reports: List[Dict[str, Any]] = []
    temp_dir = Path(tempfile.mkdtemp())
    try:
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

        found_file = False
        for filename, remote_file, local_path in ftp.iter_downloads(
            remote_path=config.ftp_remote_path,
            destination_dir=temp_dir,
            pattern=config.ftp_file_pattern,
        ):
            found_file = True
            logging.info("Processing FTP file %s", filename)
            try:
                report = _process_single_file(
                    filename=filename,
                    remote_file=remote_file,
                    local_path=local_path,
                    storage_writer=storage_writer,
                    stage_repo=stage_repo,
                    config=config,
                    notifier=notifier,
                    trigger_payload=trigger_payload,
                    sugar=sugar,
                    ftp=ftp,
                )
                processed_reports.append(report)
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to process file %s", filename)
                processed_reports.append(
                    {
                        "source_filename": filename,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        if not found_file and notifier:
            _notify_no_files(notifier)
    finally:
        for path in temp_dir.glob("*"):
            path.unlink(missing_ok=True)
        temp_dir.rmdir()

    return {
        "files_processed": len(processed_reports),
        "file_reports": processed_reports,
    }


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


def _process_single_file(
    filename: str,
    remote_file: str,
    local_path: Path,
    storage_writer: StorageWriter,
    stage_repo: StageRepository,
    config: AppConfig,
    notifier: EmailNotifier | None,
    trigger_payload: Dict[str, Any],
    sugar: SugarCrmClient,
    ftp: FtpDownloader,
) -> Dict[str, Any]:
    uploaded_file = storage_writer.upload_raw(local_path)
    source_filename = uploaded_file.blob_name.split("/")[-1]
    current_file = uploaded_file
    staged = False
    ftp_deleted = False
    try:
        ftp.delete_file(remote_file)
        ftp_deleted = True
        logging.info("Deleted FTP file %s immediately after upload", remote_file)
    except Exception:  # noqa: BLE001
        logging.exception(
            "Failed to delete FTP file %s immediately after upload; will retry later",
            remote_file,
        )
    try:
        processor = CsvProcessor(config.allowed_makes)
        try:
            records = list(processor.load(local_path))
        except HeaderValidationError as exc:
            _handle_header_error(notifier, local_path, exc)
            raise

        logging.info("Loaded %s eligible VIN records from %s", len(records), filename)
        staged_entries = stage_repo.stage_records(records, source_filename)
        staged = bool(staged_entries)

        processed_file = storage_writer.move_to_processed(uploaded_file)
        current_file = processed_file

        entries = stage_repo.fetch_by_status()
        successes = 0
        failures: List[Dict[str, str]] = []
        for entry in entries:
            record = entry.record
            try:
                vehicle_id = sugar.find_vehicle_id(record.vin)
                if not vehicle_id:
                    message = "Vehicle not found in SugarCRM"
                    stage_repo.record_error(entry.stage_id, message)
                    failures.append({"vin": record.vin, "error": message})
                    continue
                sugar.update_vehicle(vehicle_id, record)
                stage_repo.mark_pushed(entry.stage_id)
                successes += 1
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to sync VIN %s", record.vin)
                stage_repo.record_error(entry.stage_id, str(exc))
                failures.append({"vin": record.vin, "error": str(exc)})

        summary = {
            "source_filename": filename,
            "file_name": source_filename,
            "processed_records": len(records),
            "successful_updates": successes,
            "failed_updates": len(failures),
            "failures": failures,
            "trigger_payload": trigger_payload,
        }

        _notify_processing_summary(notifier, filename, summary, failures)

        summary["status"] = "success"
        return summary
    except Exception as exc:
        failure_message = f"Failed to process {filename}: {exc}"
        if current_file:
            error_file = storage_writer.move_to_error(current_file)
            current_file = error_file
        if staged:
            stage_repo.mark_failed_by_file(source_filename, failure_message)
        raise
    finally:
        if not ftp_deleted:
            try:
                ftp.delete_file(remote_file)
                logging.info("Deleted FTP file %s after processing", remote_file)
            except Exception:  # noqa: BLE001
                logging.exception("Failed to delete FTP file %s after processing", remote_file)


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
        logging.error("Unable to send notification email; SMTP settings not configured")
        return
    try:
        notifier.send(
            subject="NZTA Deregistered VIN ingest failed - header validation",
            body=message,
        )
    except Exception:  # noqa: BLE001
        logging.exception("Failed to send header validation alert email")


def _notify_processing_summary(
    notifier: EmailNotifier | None,
    filename: str,
    summary: Dict[str, Any],
    failures: List[Dict[str, str]],
) -> None:
    if not notifier:
        return
    has_failures = bool(failures)
    lines = [
        (
            "NZTA deregistered VIN sync completed with failures."
            if has_failures
            else "NZTA deregistered VIN sync completed successfully."
        ),
        f"File: {filename}",
        f"File name: {summary.get('file_name')}",
        f"Processed records: {summary.get('processed_records')}",
        f"Successful updates: {summary.get('successful_updates')}",
        f"Failed updates: {summary.get('failed_updates')}",
    ]
    if has_failures:
        lines.append("")
        lines.append("Failed VINs:")
        for item in failures:
            lines.append(f"- {item.get('vin')}: {item.get('error')}")
    body = "\n".join(lines)
    try:
        notifier.send(
            subject=(
                f"NZTA VIN sync failures for {filename}"
                if has_failures
                else f"NZTA VIN sync success for {filename}"
            ),
            body=body,
        )
    except Exception:  # noqa: BLE001
        logging.exception("Failed to send processing summary email")


def _notify_no_files(notifier: EmailNotifier) -> None:
    if not notifier:
        return
    try:
        notifier.send(
            subject="NZTA VIN sync ran with no files",
            body=(
                "The NZTA deregistered VIN sync executed successfully, "
                "but no FTP files matched the configured pattern."
            ),
        )
    except Exception:  # noqa: BLE001
        logging.exception("Failed to send no-files notification email")
