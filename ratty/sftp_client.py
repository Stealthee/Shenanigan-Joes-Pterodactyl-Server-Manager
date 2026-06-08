"""SFTP wrapper for browsing and editing a server's files.

Pterodactyl exposes each server's file manager over SFTP, with credentials
of the form `username.serverid@host:2022` and the panel account's password
(not the API key). This is a thin synchronous wrapper around paramiko --
callers are expected to run it off the UI thread.
"""

from __future__ import annotations

import stat
import threading
from dataclasses import dataclass

import paramiko


class SftpError(RuntimeError):
    pass


@dataclass
class FileEntry:
    name: str
    is_dir: bool
    size: int


class SftpClient:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None
        # paramiko's SFTPClient shares a single channel and deadlocks when used
        # concurrently from multiple threads -- serialize all operations on it.
        self._lock = threading.Lock()

    def connect(self, timeout: float = 10.0) -> None:
        with self._lock:
            try:
                transport = paramiko.Transport((self.host, self.port))
                transport.connect(username=self.username, password=self.password)
                self._transport = transport
                self._sftp = paramiko.SFTPClient.from_transport(transport)
            except (paramiko.SSHException, OSError) as exc:
                raise SftpError(f"Could not connect: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._sftp is not None:
                self._sftp.close()
                self._sftp = None
            if self._transport is not None:
                self._transport.close()
                self._transport = None

    def is_connected(self) -> bool:
        return self._transport is not None and self._transport.is_active()

    def _client(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise SftpError("Not connected")
        return self._sftp

    def list_dir(self, path: str) -> list[FileEntry]:
        with self._lock:
            entries = []
            for attr in self._client().listdir_attr(path):
                entries.append(
                    FileEntry(
                        name=attr.filename,
                        is_dir=stat.S_ISDIR(attr.st_mode or 0),
                        size=attr.st_size or 0,
                    )
                )
            entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
            return entries

    def read_file(self, path: str, max_size: int = 2_000_000) -> str:
        with self._lock:
            client = self._client()
            size = client.stat(path).st_size or 0
            if size > max_size:
                raise SftpError(f"File is too large to edit here ({size:,} bytes, limit {max_size:,})")
            with client.open(path, "r") as fh:
                data = fh.read()
            return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data

    def write_file(self, path: str, content: str) -> None:
        with self._lock:
            with self._client().open(path, "w") as fh:
                fh.write(content.encode("utf-8"))

    def delete_file(self, path: str) -> None:
        with self._lock:
            self._client().remove(path)

    def rename(self, old_path: str, new_path: str) -> None:
        with self._lock:
            self._client().rename(old_path, new_path)
