import datetime
from pathlib import Path
from typing import Optional

from google.cloud import storage


class StorageWriter:
    def __init__(self, bucket: str, prefix: str, client: Optional[storage.Client] = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client or storage.Client()

    def upload(self, local_path: Path) -> str:
        blob_name = self._build_blob_name(local_path.name)
        blob = self.client.bucket(self.bucket).blob(blob_name)
        blob.upload_from_filename(local_path)
        return f"gs://{self.bucket}/{blob_name}"

    def _build_blob_name(self, filename: str) -> str:
        timestamp = datetime.datetime.utcnow().strftime("%Y/%m/%d/%H%M%S")
        return f"{self.prefix}/{timestamp}-{filename}"
