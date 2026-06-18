# Shenanigan Joe's Pterodactyl Server Manager (SJPSM)

A desktop GUI for managing **7 Days to Die** dedicated servers — built with Python and PySide6.

SJPSM is a **Pterodactyl panel**-based server manager for **7 Days to Die** dedicated servers — built with Python and PySide6. It connects through three channels at once: **Pterodactyl** for power controls and live console, **Telnet** for live admin commands, and **SFTP** for browsing and editing files directly on the server. Everything auto-reconnects after a server reboot without restarting the app.

---

## Features

### Player Management
- Live player list showing **Name**, **Level**, **SteamID**, **Position**, and **Ping**
- Auto-refreshes every 15 seconds
- Right-click any player for a full action menu:
  - **Teleport** to another player or custom coordinates
  - **Kick** from the server
  - **Ban** with optional duration and reason
  - **Open Steam Profile** in your browser (great for investigating suspicious players)
  - **Copy SteamID** to clipboard

### Anti-Cheat Auto-Ban
Two automatic ban triggers, both configurable and remembered per server:

- **Level cheat detection** — bans players whose level rises faster than a set threshold (default 5 levels/min). Normal play is well under 1/min; obvious debug-menu cheating is 10+/min.
- **Speed hack detection** — bans players who move faster than a set threshold (default 50 m/s). Gyrocopter tops out around 35 m/s and vehicles around 20 m/s, so the default safely catches hacks while allowing legitimate travel. Threshold is adjustable if you want tighter or looser detection.

Both can be toggled on/off independently without restarting.

### Ban Management
- Full ban list in the **Banned** tab, showing the count of active bans
- Right-click any entry to **Unban** immediately or copy the identifier
- Add bans manually with duration (minutes, hours, days, or permanent) and a reason

### Live Console & Chat
- **Console** tab shows all server output in real time via the Pterodactyl websocket
- **Chat** tab shows only player chat, separated from log noise
- Send server commands from the Console tab (prefix with `/` for raw commands)
- Send chat messages from the Chat tab — they appear in-game as `Server Admin: your message`

### Power Controls
Buttons in the toolbar: **Start**, **Restart**, **Stop**, **Kill**, and **Save World**

- Stop and Kill ask for confirmation before sending
- **Save World** sends `saveworld` over telnet so you can safely reboot without rollback

### File Browser
- Browse your server's files over SFTP
- Open, edit, and save text files directly (e.g. `serverconfig.xml`, mod configs, admin files)
- Right-click to rename or delete files
- Dirty-state tracking warns before discarding unsaved edits

### Server Settings Editor
- Parses `serverconfig.xml` and presents every setting as an appropriate control:
  - **True/False** → dropdown
  - **Numbers** → spin box
  - **Text** → text field
- Settings controlled by the Pterodactyl startup command (like `TelnetPort`, `ServerMaxPlayerCount`) are shown grayed out with a note, so you know to change them in the Pterodactyl Startup tab instead
- **Reload** button re-reads the file from the server (useful after a reboot)
- **Save** writes the file back over SFTP and reminds you to restart the server

### Connection Status
- Red/green indicator dots in the toolbar for **Telnet** and **SFTP**
- Both connections auto-reconnect after a server reboot — no need to restart the app
- Server Settings auto-reloads when telnet reconnects after a reboot

### Multiple Server Profiles
- Save and manage multiple server profiles
- Each profile stores all connection details (telnet, Pterodactyl, SFTP) and its own anti-cheat settings

---

## Requirements

- Python 3.11+
- A 7 Days to Die dedicated server with Telnet enabled
- Pterodactyl panel (optional — needed for power controls and live console)
- SFTP access via Pterodactyl (optional — needed for file browser and server settings editor)

---

## Installation

```bash
git clone https://github.com/Stealthee/Shenanigan-Joes-Pterodactyl-Server-Manager.git
cd Shenanigan-Joes-Pterodactyl-Server-Manager
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Create the launcher (Linux)

```bash
tee ~/.local/bin/sjpsm > /dev/null << 'EOF'
#!/usr/bin/env bash
cd /path/to/Shenanigan-Joes-Pterodactyl-Server-Manager || exit 1
nohup .venv/bin/python -m ratty "$@" >/dev/null 2>&1 &
disown
EOF
chmod +x ~/.local/bin/sjpsm
```

Then just type `sjpsm` from any terminal. It launches detached so it won't lock up your terminal window.

### Create the launcher (Windows 11)

Create `sjpsm.bat` somewhere on your `PATH` (or just on the Desktop):

```bat
@echo off
cd /d "C:\path\to\Shenanigan-Joes-Pterodactyl-Server-Manager"
start "" /B .venv\Scripts\pythonw.exe -m ratty %*
```

Using `pythonw.exe` (instead of `python.exe`) launches without opening a console window. Double-click the `.bat` file, or run `sjpsm` from a terminal once its folder is on `PATH`.

---

## Connecting to a Server

Click **Add server** (or edit an existing profile) and fill in:

### Telnet (required for player management)
| Field | Value |
|---|---|
| Host | Your server IP, e.g. `192.168.0.155` |
| Port | Telnet port (default `8081`) |
| Password | Your `TelnetPassword` from `serverconfig.xml` |

> **Note:** If you run the server through a Pterodactyl egg, the startup command's `-TelnetPort=` variable overrides the XML value. Check the Startup tab in Pterodactyl for the actual port.

### Pterodactyl (optional — power controls + live console)
| Field | Value |
|---|---|
| Host | Panel IP or hostname, e.g. `192.168.0.155` |
| Port | Panel port (80 for HTTP, 443 for HTTPS) |
| Use HTTPS | Check if your panel uses SSL |
| API Key | Client API key from the panel (Account → API Credentials) |
| Server ID | The short server identifier, e.g. `27b040ce` |

### SFTP (optional — file browser + server settings)
| Field | Value |
|---|---|
| Host | Same IP as your panel |
| Port | SFTP port (default `2022`) |
| Username | `username.serverid` format, shown on the server's page in the panel |
| Password | Your panel account password (not the API key) |

---

## Startup Variables That Override serverconfig.xml

If you use a standard 7 Days to Die Pterodactyl egg, these settings are passed as command-line flags and override whatever is in `serverconfig.xml`. Change them in **Pterodactyl → your server → Startup**, not in the Server Settings tab:

| Startup Variable | Setting |
|---|---|
| `SERVER_PORT` | `ServerPort` |
| `MAX_PLAYERS` | `ServerMaxPlayerCount` |
| `GAME_DIFFICULTY` | `GameDifficulty` |
| `TELNET_PORT` | `TelnetPort` |
| `PASSWORD` | `TelnetPassword` |
| `SERVER_DISABLED_NETWORK_PROTOCOLS` | `ServerDisabledNetworkProtocols` |

Ratty detects these and grays them out in the editor automatically.

---

## Dependencies

| Package | Purpose |
|---|---|
| `PySide6` | Desktop GUI |
| `requests` | Pterodactyl REST API |
| `websocket-client` | Pterodactyl live console |
| `paramiko` | SFTP file access |

---

## Suggestions & Feedback

Have an idea or found a bug? Open a GitHub issue or email:

📧 **j71rivera@gmail.com**

---

## Support the Project

If Ratty saves you time managing your server, a tip is always appreciated!

💸 **[Tip on Cash App — $j71rivera](https://cash.app/$j71rivera)**

> Want to say thank you? Cash App: **$j71rivera** — every little bit keeps the project going!

---

## License

MIT — do whatever you want with it, but a shoutout is always nice.
