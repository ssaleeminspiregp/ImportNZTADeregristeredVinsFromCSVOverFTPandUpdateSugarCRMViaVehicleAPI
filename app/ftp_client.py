import ftplib
import logging
from contextlib import closing
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator, List, Tuple


class FtpDownloader:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int,
        block_size: int,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.block_size = block_size

    def list_matching(self, remote_dir: str, pattern: str) -> List[str]:
        remote_dir = remote_dir.strip("/")
        with closing(ftplib.FTP()) as ftp:
            self._login(ftp)
            if remote_dir:
                ftp.cwd(remote_dir)
            entries = ftp.nlst()
        matches = [entry for entry in entries if fnmatch(entry, pattern)]
        logging.info("Found %d files matching %s in /%s", len(matches), pattern, remote_dir or "")
        return matches

    def download_file(self, remote_dir: str, filename: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Downloading %s/%s from FTP", remote_dir or ".", filename)

        with closing(ftplib.FTP()) as ftp:
            self._login(ftp)
            if remote_dir:
                ftp.cwd(remote_dir)

            with open(destination, "wb") as outfile:
                ftp.retrbinary(f"RETR {filename}", outfile.write, blocksize=self.block_size)

        logging.info("FTP download complete: %s", destination)
        return destination

    def iter_downloads(
        self, remote_path: str, destination_dir: Path, pattern: str = "*.csv"
    ) -> Iterator[Tuple[str, Path]]:
        remote_dir = remote_path.strip("/")
        filenames = self.list_matching(remote_dir, pattern)
        for filename in filenames:
            destination = destination_dir / filename
            yield filename, self.download_file(remote_dir, filename, destination)

    def _login(self, ftp: ftplib.FTP) -> None:
        ftp.connect(self.host, self.port, timeout=self.timeout)
        ftp.login(self.username, self.password)
        ftp.set_pasv(True)
