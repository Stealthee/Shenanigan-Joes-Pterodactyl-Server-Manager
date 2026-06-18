"""SFTP wrapper for browsing and editing a server's files.

Pterodactyl exposes each server's file manager over SFTP, with credentials
of the form `username.serverid@host:2022` and the panel account's password
(not the API key). This is a thin synchronous wrapper around paramiko --
callers are expected to run it off the UI thread.
"""

from __future__ import annotations

import os
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
            client = self._client()
            entries = []
            for attr in client.listdir_attr(path):
                mode = attr.st_mode or 0
                is_dir = stat.S_ISDIR(mode)
                if stat.S_ISLNK(mode):
                    # Resolve symlinks (e.g. Steam's ".steam" link) so valid ones
                    # are navigable as directories and broken ones don't blow up
                    # the whole listing.
                    full_path = f"{path.rstrip('/')}/{attr.filename}"
                    try:
                        target = client.stat(full_path)
                    except OSError:
                        is_dir = False
                    else:
                        is_dir = stat.S_ISDIR(target.st_mode or 0)
                entries.append(
                    FileEntry(
                        name=attr.filename,
                        is_dir=is_dir,
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

    def file_exists(self, path: str) -> bool:
        with self._lock:
            try:
                self._client().stat(path)
                return True
            except OSError:
                return False

    def delete_player_data(self, steamid: str, game_world: str, game_name: str) -> list[str]:
        """Delete a player's character save files. Returns list of deleted paths."""
        candidate_dirs = [
            f"/.local/share/7DaysToDie/Saves/{game_world}/{game_name}/Player",
            f"/Saves/{game_world}/{game_name}/Player",
            f"/{game_world}/{game_name}/Player",
        ]
        extensions = (".ttp", ".map", ".fld")
        deleted = []
        with self._lock:
            client = self._client()
            for base in candidate_dirs:
                for ext in extensions:
                    path = f"{base}/{steamid}{ext}"
                    try:
                        client.stat(path)
                        client.remove(path)
                        deleted.append(path)
                    except OSError:
                        pass
                if deleted:
                    break
        return deleted

    def chmod(self, path: str, mode: int) -> None:
        with self._lock:
            self._client().chmod(path, mode)

    def chmod_recursive(self, path: str, mode: int) -> None:
        with self._lock:
            self._chmod_recursive(self._client(), path, mode)

    def _chmod_recursive(self, client: paramiko.SFTPClient, path: str, mode: int) -> None:
        client.chmod(path, mode)
        attr = client.stat(path)
        if stat.S_ISDIR(attr.st_mode or 0):
            for child in client.listdir_attr(path):
                self._chmod_recursive(client, f"{path.rstrip('/')}/{child.filename}", mode)

    def delete_dir(self, path: str) -> None:
        """Recursively delete a remote directory tree."""
        with self._lock:
            self._delete_dir_recursive(self._client(), path)

    def _delete_dir_recursive(self, client: paramiko.SFTPClient, path: str) -> None:
        for attr in client.listdir_attr(path):
            child = f"{path.rstrip('/')}/{attr.filename}"
            if stat.S_ISDIR(attr.st_mode or 0):
                self._delete_dir_recursive(client, child)
            else:
                client.remove(child)
        client.rmdir(path)

    @staticmethod
    def _ensure_remote_dir(client: paramiko.SFTPClient, path: str, mode: int = 0o755) -> None:
        try:
            client.stat(path)
        except FileNotFoundError:
            client.mkdir(path, mode)
            client.chmod(path, mode)

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a single file, then mirror its local permission bits.

        paramiko's put() always creates the remote file with the server's
        default permissions (typically 644), regardless of the local file's
        mode -- so an executable script/binary loses its +x bit on upload.
        Re-applying the local mode afterwards is what lets Pterodactyl
        actually run uploaded executables.
        """
        with self._lock:
            client = self._client()
            client.put(local_path, remote_path)
            client.chmod(remote_path, os.stat(local_path).st_mode & 0o777)

    def upload_dir(self, local_dir: str, remote_dir: str) -> int:
        """Recursively upload a local directory tree, preserving permission bits.

        Returns the number of files uploaded.
        """
        with self._lock:
            client = self._client()
            count = 0
            for root, _dirs, files in os.walk(local_dir):
                rel = os.path.relpath(root, local_dir)
                remote_root = remote_dir if rel == "." else f"{remote_dir}/{rel.replace(os.sep, '/')}"
                self._ensure_remote_dir(client, remote_root)
                for name in files:
                    local_path = os.path.join(root, name)
                    remote_path = f"{remote_root}/{name}"
                    client.put(local_path, remote_path)
                    client.chmod(remote_path, os.stat(local_path).st_mode & 0o777)
                    count += 1
            return count
