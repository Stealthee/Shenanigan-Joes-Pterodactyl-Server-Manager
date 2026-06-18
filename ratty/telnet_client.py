"""Raw-socket telnet client for the 7 Days to Die server console.

7D2D doesn't speak real telnet framing -- it's a plain text console behind a
password prompt. We open a TCP socket, read until the password prompt, send
the password, then send/receive console lines. Output isn't delimited, so we
collect lines until the socket goes quiet for a short period.
"""

from __future__ import annotations

import re
import socket
import threading
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass

PASSWORD_PROMPTS = ("please enter password", "password:")
LOGIN_OK_MARKERS = ("logon successful", "welcome")

_PLAYER_RE = re.compile(
    r"id=(?P<entity_id>\d+),\s*"
    r"(?P<name>.+?),\s*"
    r"pos=\((?P<x>-?[\d.]+),\s*(?P<y>-?[\d.]+),\s*(?P<z>-?[\d.]+)\).*?"
    r"level=(?P<level>\d+).*?"
    r"pltfmid=(?:Steam_)?(?P<steamid>\d+).*?"
    r"ip=(?P<ip>[\d.]+).*?"
    r"ping=(?P<ping>\d+)",
    re.IGNORECASE,
)

# Ban list formatting varies by server build, so we only assume the first
# token is the identifier (name or SteamID) and keep the remainder as-is --
# good enough to display, and `raw` is always available as a fallback.
_BAN_RE = re.compile(r"^(?P<id>\S+)[\s:,\-]*(?P<expires>.*)$")


@dataclass
class Player:
    entity_id: int
    name: str
    x: float
    y: float
    z: float
    level: int
    steamid: str
    ip: str
    ping: int


@dataclass
class BanEntry:
    identifier: str
    expires: str
    raw: str


class TelnetError(RuntimeError):
    pass


class TelnetClient:
    def __init__(self, host: str, port: int, password: str, on_disconnect: Callable[[], None] | None = None):
        self.host = host
        self.port = port
        self.password = password
        self._sock: socket.socket | None = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_disconnect = on_disconnect
        self._command_lock = threading.Lock()

    # -- connection lifecycle -------------------------------------------------

    def connect(self, timeout: float = 10.0) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(0.25)
        self._sock = sock
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        if self.password:
            self._authenticate(timeout)

    def _authenticate(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        buffer = ""
        while time.monotonic() < deadline:
            try:
                line = self._lines.get(timeout=0.5)
            except queue.Empty:
                continue
            buffer += line.lower() + "\n"
            if any(p in buffer for p in PASSWORD_PROMPTS):
                self._send_raw(self.password)
                return
            if any(m in buffer for m in LOGIN_OK_MARKERS):
                return
        raise TelnetError("Timed out waiting for password prompt")

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _read_loop(self) -> None:
        assert self._sock is not None
        partial = ""
        lost_connection = False
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                lost_connection = True
                break
            if not chunk:
                lost_connection = True
                break
            partial += chunk.decode("utf-8", errors="replace")
            *complete, partial = partial.split("\n")
            for line in complete:
                self._lines.put(line.rstrip("\r"))

        # Only notify on an unexpected drop -- not when close() requested the stop.
        if lost_connection and not self._stop.is_set() and self._on_disconnect:
            self._on_disconnect()

    def _send_raw(self, text: str) -> None:
        if self._sock is None:
            raise TelnetError("Not connected")
        self._sock.sendall((text + "\n").encode("utf-8"))

    # -- command execution -----------------------------------------------------

    def run_command(self, command: str, quiet_seconds: float = 0.5, max_wait: float = 5.0) -> list[str]:
        """Send a console command and collect the lines it prints.

        There's no end-of-response marker, so we keep reading until the
        connection is quiet for `quiet_seconds`, capped at `max_wait`.

        Commands are serialized with a lock -- the console has no way to tag
        a response to its request, so two commands in flight at once would
        scramble each other's output.
        """
        with self._command_lock:
            # Drain anything left over from before so it isn't mistaken for our reply.
            while not self._lines.empty():
                self._lines.get_nowait()

            self._send_raw(command)

            lines: list[str] = []
            start = time.monotonic()
            while True:
                try:
                    line = self._lines.get(timeout=quiet_seconds)
                    lines.append(line)
                except queue.Empty:
                    break
                if time.monotonic() - start > max_wait:
                    break
            return lines

    # -- high level commands ----------------------------------------------------

    def list_players(self) -> list[Player]:
        players = []
        for line in self.run_command("listplayers"):
            match = _PLAYER_RE.search(line)
            if not match:
                continue
            players.append(
                Player(
                    entity_id=int(match["entity_id"]),
                    name=match["name"].strip(),
                    x=float(match["x"]),
                    y=float(match["y"]),
                    z=float(match["z"]),
                    level=int(match["level"]),
                    steamid=match["steamid"],
                    ip=match["ip"],
                    ping=int(match["ping"]),
                )
            )
        return players

    def teleport_to_coords(self, player: str, x: float, y: float, z: float) -> list[str]:
        return self.run_command(f"teleportplayer \"{player}\" {x:.1f} {y:.1f} {z:.1f}")

    def teleport_to_player(self, player: str, target: str) -> list[str]:
        return self.run_command(f"teleportplayer \"{player}\" \"{target}\"")

    def spawn_entity(self, entity_id: int, entity_name: str, count: int = 1) -> list[str]:
        """Spawn `count` of `entity_name` near the entity/player with id `entity_id`."""
        return self.run_command(f"spawnentity {entity_id} {entity_name} {count}")

    def ban_add(self, identifier: str, duration: int = 0, unit: str = "forever", reason: str = "") -> list[str]:
        if unit == "forever":
            cmd = f"ban add \"{identifier}\" forever"
        else:
            cmd = f"ban add \"{identifier}\" {duration} {unit}"
        if reason:
            cmd += f" \"{reason}\""
        return self.run_command(cmd)

    def ban_remove(self, identifier: str) -> list[str]:
        return self.run_command(f"ban remove \"{identifier}\"")

    def ban_list(self) -> list[BanEntry]:
        entries = []
        for line in self.run_command("ban list"):
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("ban list"):
                continue
            match = _BAN_RE.search(stripped)
            if match:
                entries.append(BanEntry(identifier=match["id"], expires=match["expires"].strip(), raw=stripped))
            else:
                entries.append(BanEntry(identifier=stripped, expires="", raw=stripped))
        return entries

    def admin_add(self, identifier: str, level: int = 0, display_name: str = "") -> list[str]:
        """Grant a permission level (0 = full admin) to a player.

        `identifier` may be an online player's name/entity id, or "Steam_<steamid>"
        for an offline player.
        """
        cmd = f"admin add \"{identifier}\" {level}"
        if display_name:
            cmd += f" \"{display_name}\""
        return self.run_command(cmd)

    def admin_remove(self, identifier: str) -> list[str]:
        return self.run_command(f"admin remove \"{identifier}\"")

    def kick(self, player: str, reason: str = "") -> list[str]:
        cmd = f"kick \"{player}\""
        if reason:
            cmd += f" \"{reason}\""
        return self.run_command(cmd)

    def say(self, message: str) -> list[str]:
        return self.run_command(f"say \"{message}\"")

    _GAME_TIME_RE = re.compile(r"(Day\s+\d+.*)", re.IGNORECASE)

    def get_game_time(self) -> str:
        for line in self.run_command("gettime"):
            match = self._GAME_TIME_RE.search(line)
            if match:
                return match.group(1).strip()
        return ""
