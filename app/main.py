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
    mode = os.getenv("SERVICE_MODE", "ingest").lower()
    if request.method == "GET":
        return jsonify({"status": "ready", "mode": mode})

    event_payload = _decode_pubsub(request.get_json(silent=True))
    try:
        if mode == "sync":
            summary = execute_sync_pipeline(event_payload)
        else:
            summary = execute_ingest_pipeline(event_payload)
        return jsonify(summary)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Pipeline failed; returning success to avoid retries")
        return jsonify({"status": "error", "error": str(exc)}), 200


def execute_ingest_pipeline(trigger_payload: Dict[str, Any]) -> Dict[str, Any]:
    config = AppConfig.from_env()
    notifier = build_notifier(config.email)
    if not notifier:
        logging.info("Email notifier disabled; ingest emails will not be sent")

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
        found_file = False
        for filename, remote_file, local_path in ftp.iter_downloads(
            remote_path=config.ftp_remote_path,
            destination_dir=temp_dir,
            pattern=config.ftp_file_pattern,
        ):
            found_file = True
            logging.info("Processing FTP file %s", filename)
            try:
                report = _ingest_single_file(
                    filename=filename,
                    remote_file=remote_file,
                    local_path=local_path,
                    storage_writer=storage_writer,
                    stage_repo=stage_repo,
                    config=config,
                    notifier=notifier,
                    trigger_payload=trigger_payload,
                    ftp=ftp,
                )
                processed_reports.append(report)
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to process file %s", filename)
                error_summary = {
                    "source_filename": filename,
                    "file_name": filename,
                    "status": "error",
                    "error": str(exc),
                    "trigger_payload": trigger_payload,
                }
                processed_reports.append(error_summary)
        if not found_file:
            _notify_no_files(
                notifier,
                remote_path=config.ftp_remote_path,
                pattern=config.ftp_file_pattern,
            )
        else:
            _notify_ingest_summary(notifier, processed_reports)
    finally:
        for path in temp_dir.glob("*"):
            path.unlink(missing_ok=True)
        temp_dir.rmdir()

    return {
        "files_processed": len(processed_reports),
        "file_reports": processed_reports,
    }


def execute_sync_pipeline(trigger_payload: Dict[str, Any]) -> Dict[str, Any]:
    config = AppConfig.from_env()
    notifier = build_notifier(config.email)
    if not notifier:
        logging.info("Email notifier disabled; sync emails will not be sent")
    stage_repo = StageRepository(
        dataset=config.bq_dataset,
        table=config.bq_table,
        location=config.bq_location,
    )
    stage_repo.ensure_resources()

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

    min_age = int(os.getenv("SYNC_MIN_PENDING_AGE_MINUTES", "2"))
    entries = stage_repo.fetch_by_status(min_age_minutes=min_age)
    if not entries:
        summary = {
            "records_processed": 0,
            "successful_updates": 0,
            "failed_updates": 0,
            "file_names": [],
            "status": "success",
            "trigger_payload": trigger_payload,
        }
        _notify_sync_summary(notifier, summary, [])
        return summary

    successes = 0
    failures: List[Dict[str, str]] = []
    for entry in entries:
        record = entry.record
        try:
            vehicle_id = sugar.find_vehicle_id(record.vin)
            if not vehicle_id:
                message = "Vehicle not found in SugarCRM"
                stage_repo.record_error(entry.stage_id, message)
                failures.append(
                    {
                        "vin": record.vin,
                        "error": message,
                        "file_name": entry.source_filename or "unknown",
                    }
                )
                continue
            sugar.update_vehicle(vehicle_id, record)
            stage_repo.mark_pushed(entry.stage_id)
            successes += 1
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed to sync VIN %s", record.vin)
            stage_repo.record_error(entry.stage_id, str(exc))
            failures.append(
                {
                    "vin": record.vin,
                    "error": str(exc),
                    "file_name": entry.source_filename or "unknown",
                }
            )

    summary = {
        "records_processed": len(entries),
        "successful_updates": successes,
        "failed_updates": len(failures),
        "file_names": sorted(
            {item.get("file_name", "unknown") for item in failures}
        )
        if failures
        else [],
        "status": "success" if not failures else "partial",
        "trigger_payload": trigger_payload,
    }
    _notify_sync_summary(notifier, summary, failures)
    return summary


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


def _ingest_single_file(
    filename: str,
    remote_file: str,
    local_path: Path,
    storage_writer: StorageWriter,
    stage_repo: StageRepository,
    config: AppConfig,
    notifier: EmailNotifier | None,
    trigger_payload: Dict[str, Any],
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

        summary = {
            "source_filename": filename,
            "file_name": source_filename,
            "gcs_path": processed_file.uri,
            "staged_records": len(records),
            "trigger_payload": trigger_payload,
            "status": "success",
        }

        return summary
    except Exception as exc:
        failure_message = f"Failed to ingest {filename}: {exc}"
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


def _notify_ingest_summary(
    notifier: EmailNotifier | None, summaries: List[Dict[str, Any]]
) -> None:
    if not notifier:
        return
    if not summaries:
        return
    has_error = any(item.get("status") != "success" for item in summaries)
    lines: List[str] = []
    lines.append(
        "NZTA deregistered VIN ingest completed successfully."
        if not has_error
        else "NZTA deregistered VIN ingest completed with failures."
    )
    lines.append(f"Files processed: {len(summaries)}")
    lines.append("")
    lines.append("File results:")
    for item in summaries:
        status = item.get("status") or "unknown"
        name = item.get("source_filename") or item.get("file_name") or "unknown"
        summary_line = f"- {name}: {status}"
        details: List[str] = [summary_line]
        if status == "success":
            details.append(f"  staged_records={item.get('staged_records', 0)}")
            if item.get("gcs_path"):
                details.append(f"  gcs_path={item.get('gcs_path')}")
        if status != "success" and item.get("error"):
            details.append(f"  error={item.get('error')}")
        lines.extend(details)
    body = "\n".join(lines)
    targets = notifier.failure_recipients if has_error else notifier.success_recipients
    try:
        logging.debug("Sending aggregated ingest summary email")
        notifier.send(
            subject=(
                "NZTA ingest success"
                if not has_error
                else "NZTA ingest partial/failure"
            ),
            body=body,
            recipients=targets,
        )
    except Exception:  # noqa: BLE001
        logging.exception("Failed to send ingest summary email")


def _notify_sync_summary(
    notifier: EmailNotifier | None,
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
        f"Records processed: {summary.get('records_processed')}",
        f"Successful updates: {summary.get('successful_updates')}",
        f"Failed updates: {summary.get('failed_updates')}",
    ]
    if has_failures:
        lines.append(f"Affected files: {', '.join(summary.get('file_names') or []) or 'unknown'}")
        lines.append("")
        lines.append("Failed VINs:")
        for item in failures:
            lines.append(f"- {item.get('vin')}: {item.get('error')}")
    targets = (
        notifier.failure_recipients if has_failures else notifier.success_recipients
    )
    body = "\n".join(lines)
    try:
        logging.debug(
            "Sending sync summary email (failures=%s)", len(failures)
        )
        notifier.send(
            subject=(
                "NZTA VIN sync failures"
                if has_failures
                else "NZTA VIN sync success"
            ),
            body=body,
            recipients=targets,
        )
    except Exception:  # noqa: BLE001
        logging.exception("Failed to send sync summary email")


def _notify_no_files(
    notifier: EmailNotifier | None, remote_path: str | None = None, pattern: str | None = None
) -> None:
    if not notifier:
        return
    location = remote_path or "(root)"
    pattern_display = pattern or "*"
    body_lines = [
        "The NZTA deregistered VIN ingest executed successfully, but no FTP files matched the configured pattern.",
        f"Remote path checked: {location}",
        f"Pattern: {pattern_display}",
    ]
    try:
        logging.debug("Sending no-files notification email")
        notifier.send(
            subject="NZTA VIN ingest ran with no files",
            body="\n".join(body_lines),
            recipients=notifier.success_recipients,
        )
    except Exception:  # noqa: BLE001
        logging.exception("Failed to send no-files notification email")
