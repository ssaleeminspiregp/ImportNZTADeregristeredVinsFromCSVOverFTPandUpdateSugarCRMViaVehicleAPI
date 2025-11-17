import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.secret_loader import load_json_secret


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _require_field(source: Dict[str, Any], key: str, context: str) -> str:
    value = source.get(key)
    if value is None:
        raise RuntimeError(f"Missing '{key}' in {context}")
    return value


@dataclass(frozen=True)
class EmailSettings:
    sender: str
    recipients: List[str]
    success_recipients: List[str]
    failure_recipients: List[str]
    smtp_host: str
    smtp_port: int
    smtp_username: Optional[str]
    smtp_password: Optional[str]
    use_tls: bool
    timeout: int
    debug: bool


@dataclass(frozen=True)
class AppConfig:
    ftp_host: str
    ftp_port: int
    ftp_username: str
    ftp_password: str
    ftp_remote_path: str
    ftp_file_pattern: str
    ftp_file_pattern: str
    gcs_bucket: str
    gcs_raw_prefix: str
    gcs_processed_prefix: str
    gcs_error_prefix: str
    allowed_makes: List[str]
    sugar_base_url: str
    sugar_username: str
    sugar_password: str
    sugar_client_id: str
    sugar_client_secret: str
    sugar_platform: str
    sugar_grant_type: str
    bq_dataset: str
    bq_table: str
    bq_location: str
    ftp_timeout: int = 30
    ftp_block_size: int = 32768
    sugar_timeout: int = 30
    email: EmailSettings | None = None

    @classmethod
    def from_env(cls) -> "AppConfig":
        allowed = os.getenv("ALLOWED_MAKES", "HYUNDAI|ISUZU|RENAULT")
        allowed_makes = [
            item.strip().upper()
            for item in allowed.replace(",", "|").split("|")
            if item.strip()
        ]
        email_secret_name = os.getenv("EMAIL_SERVER_CONFIG_SECRET")
        email_secret = load_json_secret(email_secret_name) if email_secret_name else {}
        default_recips = _parse_recipients(
            os.getenv("EMAIL_RECIPIENTS") or email_secret.get("EMAIL_RECIPIENTS"),
            ["ssaleem@ib4t.co"],
        )
        success_recips = _parse_recipients(
            os.getenv("SUCCESS_EMAIL_RECIPIENTS")
            or email_secret.get("SUCCESS_EMAIL_RECIPIENTS"),
            ["ssaleem@ib4t.co"],
        )
        failure_recips = _parse_recipients(
            os.getenv("ERROR_EMAIL_RECIPIENTS")
            or email_secret.get("ERROR_EMAIL_RECIPIENTS"),
            ["ssaleem@ib4t.co"],
        )
        email_settings = None
        smtp_host = os.getenv("SMTP_HOST") or email_secret.get("SMTP_HOST")
        if smtp_host:
            sender = (
                os.getenv("EMAIL_SENDER")
                or email_secret.get("EMAIL_SENDER")
                or "noreply@ib4t.co"
            )
            email_settings = EmailSettings(
                sender=sender,
                recipients=default_recips or ["ssaleem@ib4t.co"],
                success_recipients=success_recips,
                failure_recipients=failure_recips,
                smtp_host=smtp_host,
                smtp_port=int(
                    os.getenv("SMTP_PORT") or email_secret.get("SMTP_PORT", 25)
                ),
                smtp_username=os.getenv("SMTP_USERNAME")
                or email_secret.get("SMTP_USERNAME"),
                smtp_password=os.getenv("SMTP_PASSWORD")
                or email_secret.get("SMTP_PASSWORD"),
                use_tls=_parse_bool(
                    os.getenv("SMTP_USE_TLS"), email_secret.get("SMTP_USE_TLS", True)
                ),
                timeout=_parse_timeout(
                    os.getenv("SMTP_TIMEOUT") or email_secret.get("SMTP_TIMEOUT", 30)
                ),
                debug=_parse_bool(
                    os.getenv("SMTP_DEBUG"), email_secret.get("SMTP_DEBUG", False)
                ),
            )

        sugar_platform = "GcpNztaVinDeregIntegration"

        ftp_secret = _require_env("FTP_CONFIG_SECRET")
        ftp_config = load_json_secret(ftp_secret)
        ftp_host = _require_field(ftp_config, "host", ftp_secret)
        ftp_port = int(ftp_config.get("port", 21))
        ftp_username = _require_field(ftp_config, "username", ftp_secret)
        ftp_password = _require_field(ftp_config, "password", ftp_secret)

        sugar_secret = _require_env("SUGAR_CONFIG_SECRET")
        sugar_config = load_json_secret(sugar_secret)
        sugar_base_url = _require_field(sugar_config, "base_url", sugar_secret)
        sugar_username = _require_field(sugar_config, "username", sugar_secret)
        sugar_password = _require_field(sugar_config, "password", sugar_secret)
        sugar_client_id = _require_field(sugar_config, "client_id", sugar_secret)
        sugar_client_secret = _require_field(sugar_config, "client_secret", sugar_secret)
        sugar_grant_type = sugar_config.get("grant_type", "password")

        return cls(
            ftp_host=ftp_host,
            ftp_port=ftp_port,
            ftp_username=ftp_username,
            ftp_password=ftp_password,
            ftp_remote_path=_require_env("FTP_REMOTE_PATH"),
            ftp_file_pattern=os.getenv("FTP_FILE_PATTERN", "*.csv"),
            gcs_bucket=os.getenv(
                "GCS_BUCKET", "all_brands_nzta_deregistered_vins_temp_DO_NOT_DELETE"
            ),
            gcs_raw_prefix=os.getenv("GCS_RAW_PREFIX", "raw"),
            gcs_processed_prefix=os.getenv("GCS_PROCESSED_PREFIX", "processed"),
            gcs_error_prefix=os.getenv("GCS_ERROR_PREFIX", "error"),
            allowed_makes=allowed_makes or ["HYUNDAI", "ISUZU", "RENAULT"],
            sugar_base_url=sugar_base_url,
            sugar_username=sugar_username,
            sugar_password=sugar_password,
            sugar_client_id=sugar_client_id,
            sugar_client_secret=sugar_client_secret,
            sugar_platform=sugar_platform,
            sugar_grant_type=sugar_grant_type,
            bq_dataset=os.getenv("BQ_STAGE_DATASET", "ds_nzta"),
            bq_table=os.getenv(
                "BQ_STAGE_TABLE", "dl_all_brands_deregistered_vins_stage"
            ),
            bq_location=os.getenv("BQ_STAGE_LOCATION", "australia-southeast1"),
            email=email_settings,
        )
def _parse_recipients(value: Optional[str], default: List[str]) -> List[str]:
    if not value:
        return default
    cleaned = value.replace("|", ",")
    parsed = [item.strip() for item in cleaned.split(",") if item.strip()]
    return parsed or default


def _parse_bool(value: Optional[str], fallback: bool) -> bool:
    if value is None:
        return bool(fallback)
    return str(value).strip().lower() not in {"false", "0", "", "no", "none"}


def _parse_timeout(value: Optional[str | int], fallback: int) -> int:
    try:
        parsed = int(value) if value is not None else int(fallback)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(5, min(parsed, 120))
