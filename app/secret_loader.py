import json
import os
from functools import lru_cache
from typing import Any, Dict

from google.cloud import secretmanager


_client: secretmanager.SecretManagerServiceClient | None = None


def _get_client() -> secretmanager.SecretManagerServiceClient:
    global _client
    if _client is None:
        _client = secretmanager.SecretManagerServiceClient()
    return _client


def _build_resource_name(secret_name: str, version: str) -> str:
    if secret_name.startswith("projects/"):
        return f"{secret_name}/versions/{version}"

    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or os.getenv("PROJECT_ID")
    )
    if not project_id:
        raise RuntimeError(
            "Missing project ID; set GOOGLE_CLOUD_PROJECT when referencing short secret names."
        )
    return f"projects/{project_id}/secrets/{secret_name}/versions/{version}"


@lru_cache(maxsize=32)
def load_secret(secret_name: str, version: str = "latest") -> bytes:
    resource = _build_resource_name(secret_name, version)
    response = _get_client().access_secret_version(name=resource)
    return response.payload.data


def load_json_secret(secret_name: str, version: str = "latest") -> Dict[str, Any]:
    payload = load_secret(secret_name, version)
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Secret {secret_name} does not contain valid JSON") from exc
