"""Persistence for saved server profiles.

Stored as plain JSON under the user's config directory. Note this includes
the telnet password, Pterodactyl API key, and SFTP password in plain text on
disk -- fine for a single-user admin tool, but worth knowing if the machine
is shared.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from ratty.config import ServerConfig


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ratty"


def _servers_path() -> Path:
    return _config_dir() / "servers.json"


def load_servers() -> list[ServerConfig]:
    path = _servers_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    servers = []
    field_names = set(ServerConfig.__dataclass_fields__)
    for entry in raw:
        servers.append(ServerConfig(**{k: v for k, v in entry.items() if k in field_names}))
    return servers


def save_servers(servers: list[ServerConfig]) -> None:
    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    path = _servers_path()
    path.write_text(json.dumps([asdict(s) for s in servers], indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass
