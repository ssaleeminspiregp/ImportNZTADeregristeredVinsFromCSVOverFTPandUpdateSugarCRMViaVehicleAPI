import os
from dataclasses import dataclass
from typing import Dict, List


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


@dataclass(frozen=True)
class AppConfig:
    ftp_host: str
    ftp_port: int
    ftp_username: str
    ftp_password: str
    ftp_remote_path: str
    gcs_bucket: str
    gcs_prefix: str
    allowed_makes: List[str]
    sugar_base_url: str
    sugar_username: str
    sugar_password: str
    sugar_client_id: str
    sugar_client_secret: str
    sugar_platform: str
    team_ids: Dict[str, str]
    bq_dataset: str
    bq_table: str
    bq_location: str
    ftp_timeout: int = 30
    ftp_block_size: int = 32768
    sugar_timeout: int = 30

    @classmethod
    def from_env(cls) -> "AppConfig":
        allowed = os.getenv("ALLOWED_MAKES", "HYUNDAI,ISUZU,RENAULT")
        allowed_makes = [item.strip().upper() for item in allowed.split(",") if item.strip()]
        team_ids = {
            "HYUNDAI": os.getenv("TEAM_ID_HYUNDAI"),
            "ISUZU": os.getenv("TEAM_ID_ISUZU"),
            "RENAULT": os.getenv("TEAM_ID_RENAULT"),
        }
        team_ids = {key: value for key, value in team_ids.items() if value}

        return cls(
            ftp_host=_require_env("FTP_HOST"),
            ftp_port=int(os.getenv("FTP_PORT", "21")),
            ftp_username=_require_env("FTP_USERNAME"),
            ftp_password=_require_env("FTP_PASSWORD"),
            ftp_remote_path=_require_env("FTP_REMOTE_PATH"),
            gcs_bucket=_require_env("GCS_BUCKET"),
            gcs_prefix=os.getenv("GCS_PREFIX", "nzta/raw"),
            allowed_makes=allowed_makes or ["HYUNDAI", "ISUZU", "RENAULT"],
            sugar_base_url=_require_env("SUGAR_BASE_URL"),
            sugar_username=_require_env("SUGAR_USERNAME"),
            sugar_password=_require_env("SUGAR_PASSWORD"),
            sugar_client_id=_require_env("SUGAR_CLIENT_ID"),
            sugar_client_secret=_require_env("SUGAR_CLIENT_SECRET"),
            sugar_platform=os.getenv("SUGAR_PLATFORM", "automote_api"),
            team_ids=team_ids,
            bq_dataset=os.getenv("BQ_STAGE_DATASET", "ds_nzta_deregistered_vins"),
            bq_table=os.getenv(
                "BQ_STAGE_TABLE", "dl_all_brands_nzta_deregistered_vins_stage"
            ),
            bq_location=os.getenv("BQ_STAGE_LOCATION", "australia-southeast1"),
        )
