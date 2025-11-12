import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from google.cloud import storage


@dataclass
class StoredFile:
    blob_name: str
    uri: str
    original_name: str


class StorageWriter:
    def __init__(
        self,
        bucket: str,
        raw_prefix: str,
        processed_prefix: str,
        error_prefix: str,
        client: Optional[storage.Client] = None,
    ) -> None:
        self.bucket = bucket
        self.raw_prefix = raw_prefix.strip("/")
        self.processed_prefix = processed_prefix.strip("/")
        self.error_prefix = error_prefix.strip("/")
        self.client = client or storage.Client()

    def upload_raw(self, local_path: Path) -> StoredFile:
        blob_name = self._build_blob_name(self.raw_prefix, local_path.name)
        blob = self.client.bucket(self.bucket).blob(blob_name)
        blob.upload_from_filename(local_path)
        logging.info("Uploaded raw file to gs://%s/%s", self.bucket, blob_name)
        return StoredFile(
            blob_name=blob_name,
            uri=f"gs://{self.bucket}/{blob_name}",
            original_name=local_path.name,
        )

    def move_to_processed(self, stored: StoredFile) -> StoredFile:
        return self._move(stored, self.processed_prefix, "processed")

    def move_to_error(self, stored: StoredFile) -> StoredFile:
        return self._move(stored, self.error_prefix, "error")

    def _move(self, stored: StoredFile, destination_prefix: str, label: str) -> StoredFile:
        bucket = self.client.bucket(self.bucket)
        source_blob = bucket.blob(stored.blob_name)
        destination_name = self._build_blob_name(destination_prefix, stored.original_name)
        bucket.rename_blob(source_blob, destination_name)
        logging.info(
            "Moved file from %s to %s (%s bucket segment)",
            stored.blob_name,
            destination_name,
            label,
        )
        return StoredFile(
            blob_name=destination_name,
            uri=f"gs://{self.bucket}/{destination_name}",
            original_name=stored.original_name,
        )

    def _build_blob_name(self, prefix: str, filename: str) -> str:
        timestamp = datetime.datetime.utcnow().strftime("%Y/%m/%d/%H%M%S")
        cleaned_prefix = prefix.strip("/")
        core_name = f"{timestamp}-{filename}"
        return f"{cleaned_prefix}/{core_name}" if cleaned_prefix else core_name
