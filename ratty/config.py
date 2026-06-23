"""Connection settings for a single 7 Days to Die server.

Three independent channels are configured here:
  * Telnet -- the game server's own admin console (player list, teleport, bans).
  * Pterodactyl -- the panel's Client API + websocket (power actions, live
    console/chat, sending commands as an alternative to telnet).
  * SFTP -- the panel's file manager access, for browsing and editing the
    server's files directly (e.g. sftp://username.serverid@host:2022).

Defaults reflect what each service ships with; every value is editable.
"""

from dataclasses import dataclass, field

DEFAULT_TELNET_PORT = 8081
DEFAULT_PTERODACTYL_PORT = 443
DEFAULT_SFTP_PORT = 2022

# One is picked at random and broadcast (via telnet `say`) whenever a player dies.
DEFAULT_DEATH_MESSAGES: list[str] = [
    "{name} just became zombie chow.",
    "{name} forgot which way was up. RIP.",
    "{name} tripped over their own ego and died.",
    "Well, {name} is dead. Worth it? Probably not.",
    "{name} just donated their loot to the wasteland. How generous.",
    "{name} found out the hard way that zombies bite back.",
    "Another one bites the dust -- looking at you, {name}.",
    "{name} has respawned in the great beyond. Send snacks.",
    "{name} died doing what they loved: dying.",
    "RIP {name}. The horde sends its regards.",
    "{name} just rage-quit life. Temporarily.",
    "{name} got outplayed by a zombie. Embarrassing.",
    "{name} is now one with the dirt.",
    "Press F for {name}. They've left the chat (permanently, for now).",
    "{name} discovered fall damage is still undefeated.",
    "{name} just lost a fight to a single zombie. Legendary.",
    "{name} explored the map a little too aggressively.",
    "{name} forgot to eat. Or maybe it was the zombies. Who's to say.",
    "{name} has joined the 'oops' club. Population: growing.",
    "{name} is taking an involuntary nap. See you at spawn.",
]

# One is picked at random and broadcast whenever a player joins the server.
DEFAULT_JOIN_MESSAGES: list[str] = [
    "{name} has entered the wasteland. Try not to die in the first five minutes.",
    "Look who decided to show up -- {name} is here.",
    "{name} just joined. Hide your loot, everyone.",
    "Everybody act normal, {name} just walked in.",
    "{name} has spawned. The horde already knows.",
]

DEFAULT_LEVELUP_MESSAGE = "Congrats {name}, you just hit level {level}! Keep grinding."
DEFAULT_HORDE_TODAY_MESSAGE = "Tonight's the night -- the Blood Moon horde is here. Good luck out there!"
DEFAULT_HORDE_SOON_MESSAGE = "Heads up survivors -- Blood Moon horde in 2 days. Stock up and fortify!"
DEFAULT_RESTART_WARNING_MESSAGE = "Server restarting in {minutes} minute(s) -- back shortly!"


@dataclass
class ServerConfig:
    name: str = "My Server"
    is_default: bool = False

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

    # Roster of every player ever seen on this server, so the Players tab can
    # show people as "Offline" instead of forgetting them on disconnect.
    # Each entry: {"steamid": str, "name": str, "last_seen": str}
    known_players: list[dict] = field(default_factory=list)

    mods_dir: str = "/Mods"
    locked_mods: list[str] = field(default_factory=list)

    autorestart_enabled: bool = False
    autorestart_mode: str = "time"  # "time" (daily HH:MM) or "interval" (every N hours)
    autorestart_time: str = "04:00"
    autorestart_interval_hours: float = 6.0
    autorestart_warning_message: str = DEFAULT_RESTART_WARNING_MESSAGE

    broadcast_death_enabled: bool = True
    broadcast_death_messages: list[str] = field(default_factory=lambda: list(DEFAULT_DEATH_MESSAGES))

    broadcast_join_enabled: bool = True
    broadcast_join_messages: list[str] = field(default_factory=lambda: list(DEFAULT_JOIN_MESSAGES))

    broadcast_levelup_enabled: bool = True
    broadcast_levelup_message: str = DEFAULT_LEVELUP_MESSAGE

    broadcast_horde_enabled: bool = True
    broadcast_horde_frequency_days: int = 7
    broadcast_horde_today_message: str = DEFAULT_HORDE_TODAY_MESSAGE
    broadcast_horde_soon_message: str = DEFAULT_HORDE_SOON_MESSAGE

    @property
    def pterodactyl_base_url(self) -> str:
        scheme = "https" if self.pterodactyl_use_tls else "http"
        default_port = 443 if self.pterodactyl_use_tls else 80
        if self.pterodactyl_port == default_port:
            return f"{scheme}://{self.pterodactyl_host}"
        return f"{scheme}://{self.pterodactyl_host}:{self.pterodactyl_port}"
