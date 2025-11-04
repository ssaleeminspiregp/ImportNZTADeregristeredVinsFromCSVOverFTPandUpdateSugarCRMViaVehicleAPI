import ftplib
import logging
from contextlib import closing
from pathlib import Path


class FtpDownloader:
    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: int, block_size: int) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.block_size = block_size

    def download(self, remote_path: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        remote_dir, filename = self._split_path(remote_path)
        logging.info("Downloading %s from FTP", filename)

        with closing(ftplib.FTP()) as ftp:
            ftp.connect(self.host, self.port, timeout=self.timeout)
            ftp.login(self.username, self.password)
            ftp.set_pasv(True)
            if remote_dir:
                ftp.cwd(remote_dir)

            with open(destination, "wb") as outfile:
                ftp.retrbinary(f"RETR {filename}", outfile.write, blocksize=self.block_size)

        logging.info("FTP download complete: %s", destination)
        return destination

    @staticmethod
    def _split_path(remote_path: str) -> tuple[str, str]:
        remote_path = remote_path.lstrip("/")
        parts = remote_path.rsplit("/", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
