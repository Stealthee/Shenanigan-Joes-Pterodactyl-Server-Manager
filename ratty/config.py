"""Connection settings for a single 7 Days to Die server.

Three independent channels are configured here:
  * Telnet -- the game server's own admin console (player list, teleport, bans).
  * Pterodactyl -- the panel's Client API + websocket (power actions, live
    console/chat, sending commands as an alternative to telnet).
  * SFTP -- the panel's file manager access, for browsing and editing the
    server's files directly (e.g. sftp://username.serverid@host:2022).

Defaults reflect what each service ships with; every value is editable.
"""

from dataclasses import dataclass

DEFAULT_TELNET_PORT = 8081
DEFAULT_PTERODACTYL_PORT = 443
DEFAULT_SFTP_PORT = 2022


@dataclass
class ServerConfig:
    name: str = "My Server"

    telnet_host: str = ""
    telnet_port: int = DEFAULT_TELNET_PORT
    telnet_password: str = ""

    pterodactyl_host: str = ""
    pterodactyl_port: int = DEFAULT_PTERODACTYL_PORT
    pterodactyl_use_tls: bool = True
    pterodactyl_api_key: str = ""
    pterodactyl_server_id: str = ""

    sftp_host: str = ""
    sftp_port: int = DEFAULT_SFTP_PORT
    sftp_username: str = ""
    sftp_password: str = ""

    autoban_level_enabled: bool = False
    autoban_level_threshold: int = 5    # levels per minute
    autoban_speed_enabled: bool = False
    autoban_speed_threshold: int = 50   # metres per second

    @property
    def pterodactyl_base_url(self) -> str:
        scheme = "https" if self.pterodactyl_use_tls else "http"
        default_port = 443 if self.pterodactyl_use_tls else 80
        if self.pterodactyl_port == default_port:
            return f"{scheme}://{self.pterodactyl_host}"
        return f"{scheme}://{self.pterodactyl_host}:{self.pterodactyl_port}"
