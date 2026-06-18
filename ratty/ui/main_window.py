"""Main application window: player list, ban list, live console/chat, power controls."""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ratty.config import ServerConfig
from ratty.pterodactyl_client import ConsoleStream, PterodactylClient, PterodactylError
from ratty.server_config_xml import XmlProperty, apply_property_changes, parse_properties
from ratty.sftp_client import FileEntry, SftpClient, SftpError
from ratty.telnet_client import BanEntry, Player, TelnetClient, TelnetError


SERVER_CONFIG_PATH = "/serverconfig.xml"

# Properties whose values are passed as command-line flags in the Pterodactyl
# egg startup command, overriding whatever is written in serverconfig.xml.
_STARTUP_OVERRIDES: frozenset[str] = frozenset({
    "ServerPort",
    "ServerDisabledNetworkProtocols",
    "ServerMaxPlayerCount",
    "GameDifficulty",
    "TelnetPort",
    "TelnetPassword",
    "TelnetEnabled",
    "ControlPanelEnabled",
})

# Console output lines for in-game chat look like:
#   Chat (from 'Steam_765xxxxx', entity id '171', to 'Global'): 'PlayerName': hello
_CHAT_LINE_RE = re.compile(r"Chat \([^)]*\):\s*(?P<text>.*)$")

# Quick-pick entries for the player "Spawn" submenu, passed straight to the
# `spawnentity` console command. Pulled from this server's own entity class list
# (`spawnentityat` with no args, run against V 3.0.0 b252) -- names are version-
# specific and drift between game updates, so re-check that list if these start
# coming back "Entity 'x' not found".
_ZOMBIE_TYPES: tuple[tuple[str, str], ...] = (
    ("Walker", "zombieArlene"),
    ("Marlene", "zombieMarlene"),
    ("Party Girl", "zombiePartyGirl"),
    ("Nurse", "zombieNurse"),
    ("Joe", "zombieJoe"),
    ("Steve", "zombieSteve"),
    ("Tom Clark", "zombieTomClark"),
    ("Business Man", "zombieBusinessMan"),
    ("Burnt", "zombieBurnt"),
    ("Rancher", "zombieRancher"),
    ("Chuck", "zombieChuck"),
    ("Spider", "zombieSpider"),
    ("Boe", "zombieBoe"),
    ("Hazmat", "zombieMaleHazmat"),
    ("Janitor", "zombieJanitor"),
    ("Inmate", "zombieInmate"),
    ("Moe", "zombieMoe"),
    ("Lab Worker", "zombieLab"),
    ("Darlene", "zombieDarlene"),
    ("Yo Mama", "zombieYo"),
    ("Utility Worker", "zombieUtilityWorker"),
    ("Biker", "zombieBiker"),
    ("Lumberjack", "zombieLumberjack"),
    ("Fat Female", "zombieFemaleFat"),
    ("Fat Hawaiian (exploder)", "zombieFatHawaiian"),
    ("Bowler", "zombieBowler"),
    ("Fat Cop (acid)", "zombieFatCop"),
    ("Skateboarder", "zombieSkateboarder"),
    ("Soldier", "zombieSoldier"),
    ("Screamer (calls a horde!)", "zombieScreamer"),
    ("Mutated", "zombieMutated"),
    ("Demolition (explosive)", "zombieDemolition"),
    ("Crawler", "zombieSteveCrawler"),
    ("Feral Wight", "zombieWightFeral"),
    ("Radiated Wight", "zombieWightRadiated"),
)

_ANIMAL_TYPES: tuple[tuple[str, str], ...] = (
    ("Rabbit", "animalRabbit"),
    ("Chicken", "animalChicken"),
    ("Angry Chicken", "animalChickenHostile"),
    ("Stag", "animalStag"),
    ("Doe", "animalDoe"),
    ("Boar", "animalBoar"),
    ("Coyote", "animalCoyote"),
    ("Wolf", "animalWolf"),
    ("Dire Wolf", "animalDireWolf"),
    ("Bear", "animalBear"),
    ("Mountain Lion", "animalMountainLion"),
    ("Snake", "animalSnake"),
    ("Zombie Dog", "animalZombieDog"),
    ("Zombie Bear", "animalZombieBear"),
    ("Zombie Boar", "animalZombieBoar"),
    ("Zombie Vulture", "animalZombieVulture"),
)


class _Bridge(QObject):
    """Marshals results from background threads back onto the UI thread."""

    result = Signal(str, object)
    error = Signal(str, str)


class TeleportToCoordsDialog(QDialog):
    def __init__(self, player_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Teleport {player_name} to coordinates")
        form = QFormLayout(self)
        self.x = QDoubleSpinBox(); self.x.setRange(-30000, 30000); self.x.setDecimals(1)
        self.y = QDoubleSpinBox(); self.y.setRange(-1000, 1000); self.y.setDecimals(1)
        self.z = QDoubleSpinBox(); self.z.setRange(-30000, 30000); self.z.setDecimals(1)
        form.addRow("X", self.x)
        form.addRow("Y", self.y)
        form.addRow("Z", self.z)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def coords(self) -> tuple[float, float, float]:
        return self.x.value(), self.y.value(), self.z.value()


class SpawnEntityDialog(QDialog):
    def __init__(self, player_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Spawn entity at {player_name}")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. zombieArlene, wolf, bear")
        self.count = QSpinBox(); self.count.setRange(1, 20); self.count.setValue(1)
        form.addRow("Entity class name", self.name)
        form.addRow("Count", self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, int]:
        return self.name.text().strip(), self.count.value()


class BanDialog(QDialog):
    UNITS = ["forever", "minutes", "hours", "days", "weeks", "months", "years"]

    def __init__(self, identifier: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Ban {identifier}")
        form = QFormLayout(self)
        self.identifier = QLineEdit(identifier)
        self.unit = QLineEdit()
        self.duration = QSpinBox(); self.duration.setRange(0, 100000)
        from PySide6.QtWidgets import QComboBox
        self.unit_box = QComboBox(); self.unit_box.addItems(self.UNITS)
        self.reason = QLineEdit()
        form.addRow("Identifier", self.identifier)
        form.addRow("Duration", self.duration)
        form.addRow("Unit", self.unit_box)
        form.addRow("Reason", self.reason)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return self.identifier.text().strip(), self.duration.value(), self.unit_box.currentText(), self.reason.text().strip()


class MainWindow(QMainWindow):
    def __init__(self, config: ServerConfig, save_config_callback=None):
        super().__init__()
        self.config = config
        self._save_config_callback = save_config_callback
        self.setWindowTitle(f"Shenanigan Joe's Pterodactyl Server Manager -- {config.name}")
        self.resize(1000, 650)
        self._restore_window_geometry()

        self._telnet: TelnetClient | None = None
        self._ptero: PterodactylClient | None = None
        self._console: ConsoleStream | None = None
        self._sftp: SftpClient | None = None
        self._players: list[Player] = []

        # Every player ever seen on this server, keyed by steamid, so the
        # Players tab can keep showing people (as "Offline") after they leave.
        self._known_players: dict[str, dict] = {
            p["steamid"]: dict(p) for p in config.known_players if p.get("steamid")
        }

        # steamid -> (x, y, z) position immediately before our last teleport,
        # so a right-click "Return" can put the player back.
        self._teleport_history: dict[str, tuple[float, float, float]] = {}

        # steamid -> (first_seen_level, first_seen_timestamp)
        self._level_history: dict[str, tuple[int, float]] = {}
        # steamid -> (x, y, z, timestamp) of last known position
        self._position_history: dict[str, tuple[float, float, float, float]] = {}

        self._sftp_cwd = "/"
        self._sftp_entries: list[FileEntry] = []
        self._sftp_open_path: str | None = None
        self._sftp_dirty = False
        self._sftp_loading = False

        self._mods_entries: list[str] = []

        self._settings_xml: str | None = None
        self._settings_properties: list[XmlProperty] = []
        self._settings_widgets: dict[str, QWidget] = {}
        self._settings_dirty = False
        self._settings_loading = False

        self._telnet_reconnect_timer: QTimer | None = None
        self._telnet_status_dot: QLabel | None = None

        self._sftp_status_dot: QLabel | None = None
        self._sftp_reconnect_timer: QTimer | None = None
        self._sftp_health_timer: QTimer | None = None
        self._game_time_label: QLabel | None = None

        self._bridge = _Bridge()
        self._bridge.result.connect(self._on_async_result)
        self._bridge.error.connect(self._on_async_error)

        self._build_ui()
        self._connect_backends()

        self._player_refresh_timer = QTimer(self)
        self._player_refresh_timer.timeout.connect(self.refresh_players)
        self._player_refresh_timer.start(self.PLAYER_REFRESH_INTERVAL_MS)

    # -- window geometry ----------------------------------------------------------

    def _restore_window_geometry(self) -> None:
        geometry = QSettings("ratty", "sjpsm").value("main_window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
        QSettings("ratty", "sjpsm").setValue("main_window/geometry", self.saveGeometry())
        super().closeEvent(event)

    # -- UI construction --------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_power_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_left_tabs())
        splitter.addWidget(self._build_console_panel())
        splitter.setSizes([550, 450])

        self.statusBar().showMessage("Connecting...")

    def _build_power_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(QLabel(f"<b>{self.config.name}</b>"))

        if self.config.telnet_host:
            self._telnet_status_dot = self._add_status_indicator(bar, "Telnet")
            self._set_telnet_status(False)

        if self.config.sftp_host:
            self._sftp_status_dot = self._add_status_indicator(bar, "SFTP")
            self._set_sftp_status(False)

        if self.config.telnet_host:
            self._game_time_label = QLabel("")
            self._game_time_label.setStyleSheet("color: white; font-weight: bold; margin-left: 6px;")
            self._game_time_label.setToolTip("Current in-game day and time")
            bar.addWidget(self._game_time_label)

        bar.addStretch(1)

        has_ptero = bool(self.config.pterodactyl_host)
        no_ptero_tip = "Requires a Pterodactyl connection (not configured for this server)"

        self.power_buttons: list[QPushButton] = []
        for label, action in (("Start", "start"), ("Restart", "restart"), ("Stop", "stop"), ("Kill", "kill")):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, a=action: self._send_power_action(a))
            if not has_ptero:
                btn.setEnabled(False)
                btn.setToolTip(no_ptero_tip)
            bar.addWidget(btn)
            self.power_buttons.append(btn)

        if not has_ptero:
            note = QLabel("(power controls need Pterodactyl)")
            note.setStyleSheet("color: palette(placeholder-text);")
            bar.addWidget(note)

        has_telnet = bool(self.config.telnet_host)
        save_btn = QPushButton("Save World")
        save_btn.clicked.connect(self._save_world)
        if not has_telnet:
            save_btn.setEnabled(False)
            save_btn.setToolTip("Requires a Telnet connection")
        bar.addWidget(save_btn)

        refresh = QPushButton("Refresh players")
        refresh.clicked.connect(self.refresh_players)
        bar.addWidget(refresh)

        bar.addStretch(1)

        suggest_btn = QPushButton("💬 Suggestions")
        suggest_btn.setToolTip("Send suggestions or bug reports to j71rivera@gmail.com")
        suggest_btn.clicked.connect(self._open_suggestions)
        suggest_btn.setFlat(True)
        bar.addWidget(suggest_btn)

        donate_btn = QPushButton("💛 Donate")
        donate_btn.setToolTip("Want to say thank you? Tip on Cash App: $j71rivera")
        donate_btn.clicked.connect(self._open_donate)
        donate_btn.setFlat(True)
        donate_btn.setStyleSheet("color: #f0a500; font-weight: bold;")
        bar.addWidget(donate_btn)

        return bar

    @staticmethod
    def _add_status_indicator(bar: QHBoxLayout, label: str) -> QLabel:
        dot = QLabel()
        dot.setFixedWidth(14)
        bar.addWidget(dot)
        bar.addWidget(QLabel(label))
        return dot

    @staticmethod
    def _paint_status_dot(dot: QLabel, connected: bool, label: str) -> None:
        color = "#2ecc71" if connected else "#e74c3c"
        dot.setText("●")
        dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        dot.setToolTip(f"{label} {'connected' if connected else 'disconnected'}")

    def _build_left_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_player_table(), "Players")
        tabs.addTab(self._build_ban_panel(), "Banned")
        tabs.addTab(self._build_files_panel(), "Files")
        tabs.addTab(self._build_settings_panel(), "Server Settings")
        self._mods_panel = self._build_mods_panel()
        tabs.addTab(self._mods_panel, "Mods")
        self._left_tabs = tabs
        return tabs

    def _build_player_table(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        level_row = QHBoxLayout()
        self.autoban_level_enabled = QComboBox()
        self.autoban_level_enabled.addItems(["Level cheat ban: OFF", "Level cheat ban: ON"])
        self.autoban_level_enabled.setCurrentIndex(1 if self.config.autoban_level_enabled else 0)
        level_row.addWidget(self.autoban_level_enabled)
        level_row.addWidget(QLabel("Max lvl/min:"))
        self.autoban_level_threshold = QSpinBox()
        self.autoban_level_threshold.setRange(1, 1000)
        self.autoban_level_threshold.setValue(self.config.autoban_level_threshold)
        self.autoban_level_threshold.setToolTip(
            "Ban a player if their level rises faster than this many levels per minute.\n"
            "Normal play is well under 1/min; obvious cheating is typically 10+/min."
        )
        level_row.addWidget(self.autoban_level_threshold)
        level_row.addStretch(1)
        layout.addLayout(level_row)

        speed_row = QHBoxLayout()
        self.autoban_speed_enabled = QComboBox()
        self.autoban_speed_enabled.addItems(["Speed hack ban: OFF", "Speed hack ban: ON"])
        self.autoban_speed_enabled.setCurrentIndex(1 if self.config.autoban_speed_enabled else 0)
        speed_row.addWidget(self.autoban_speed_enabled)
        speed_row.addWidget(QLabel("Max m/s:"))
        self.autoban_speed_threshold = QSpinBox()
        self.autoban_speed_threshold.setRange(1, 5000)
        self.autoban_speed_threshold.setValue(self.config.autoban_speed_threshold)
        self.autoban_speed_threshold.setToolTip(
            "Ban a player if they move faster than this many metres per second.\n"
            "Gyrocopter tops out ~35 m/s, vehicles ~20 m/s -- set above 40 to avoid false positives.\n"
            "Obvious speed hacks are typically 100+ m/s."
        )
        speed_row.addWidget(self.autoban_speed_threshold)
        speed_row.addStretch(1)
        layout.addLayout(speed_row)

        for widget in (self.autoban_level_enabled, self.autoban_level_threshold,
                       self.autoban_speed_enabled, self.autoban_speed_threshold):
            widget.currentIndexChanged.connect(self._save_autoban_settings) if hasattr(widget, 'currentIndexChanged') else widget.valueChanged.connect(self._save_autoban_settings)

        self.player_table = QTableWidget(0, 7)
        self.player_table.setHorizontalHeaderLabels(["Name", "Status", "Level", "SteamID", "Position", "Ping", "Last Seen"])
        self.player_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.player_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.player_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.player_table.customContextMenuRequested.connect(self._show_player_menu)
        header = self.player_table.horizontalHeader()
        from PySide6.QtWidgets import QHeaderView
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)          # Name fills spare space
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)             # Status
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)             # Level
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)             # SteamID
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)             # Position
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)             # Ping
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)             # Last Seen
        header.resizeSection(1, 60)
        header.resizeSection(2, 55)
        header.resizeSection(3, 160)
        header.resizeSection(4, 160)
        header.resizeSection(5, 55)
        header.resizeSection(6, 120)
        layout.addWidget(self.player_table)

        history_row = QHBoxLayout()
        history_row.addStretch(1)
        clear_history_btn = QPushButton("Clear Player History")
        clear_history_btn.setToolTip("Remove everyone from the offline player list -- use after a server wipe.")
        clear_history_btn.clicked.connect(self._clear_player_history)
        history_row.addWidget(clear_history_btn)
        layout.addLayout(history_row)
        return wrapper

    def _build_ban_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        self.ban_table = QTableWidget(0, 2)
        self.ban_table.setHorizontalHeaderLabels(["Identifier", "Expires"])
        self.ban_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ban_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ban_table.horizontalHeader().setStretchLastSection(True)
        self.ban_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ban_table.customContextMenuRequested.connect(self._show_ban_menu)
        layout.addWidget(self.ban_table)

        buttons = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_bans)
        add_btn = QPushButton("Add ban...")
        add_btn.clicked.connect(self._add_ban_dialog)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected_ban)
        for b in (refresh_btn, add_btn, remove_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return wrapper

    def _build_files_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        if not self.config.sftp_host:
            note = QLabel(
                "SFTP is not configured for this server -- add it in the\n"
                "connection settings to browse and edit files."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(note)

        nav_row = QHBoxLayout()
        up_btn = QPushButton("Up")
        up_btn.clicked.connect(self._sftp_go_up)
        self.sftp_path_edit = QLineEdit(self._sftp_cwd)
        self.sftp_path_edit.setStyleSheet("font-family: monospace;")
        self.sftp_path_edit.setPlaceholderText("/path/to/folder")
        self.sftp_path_edit.returnPressed.connect(self._sftp_navigate_to_typed_path)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(lambda: self._sftp_browse(self._sftp_cwd))
        upload_files_btn = QPushButton("Upload Files...")
        upload_files_btn.clicked.connect(self._upload_sftp_files)
        upload_folder_btn = QPushButton("Upload Folder...")
        upload_folder_btn.clicked.connect(self._upload_sftp_folder)
        nav_row.addWidget(up_btn)
        nav_row.addWidget(self.sftp_path_edit, 1)
        nav_row.addWidget(upload_files_btn)
        nav_row.addWidget(upload_folder_btn)
        nav_row.addWidget(refresh_btn)
        layout.addLayout(nav_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        self.sftp_list = QListWidget()
        self.sftp_list.itemDoubleClicked.connect(self._sftp_entry_activated)
        self.sftp_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sftp_list.customContextMenuRequested.connect(self._show_sftp_menu)
        splitter.addWidget(self.sftp_list)

        editor_wrapper = QWidget()
        editor_layout = QVBoxLayout(editor_wrapper)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.sftp_editor_label = QLabel("(no file open)")
        self.sftp_editor_label.setStyleSheet("font-family: monospace;")
        editor_layout.addWidget(self.sftp_editor_label)
        self.sftp_editor = QPlainTextEdit()
        self.sftp_editor.setPlaceholderText("Double-click a file on the left to view/edit it here")
        self.sftp_editor.textChanged.connect(self._on_sftp_editor_changed)
        editor_layout.addWidget(self.sftp_editor, 1)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.sftp_save_btn = QPushButton("Save")
        self.sftp_save_btn.setEnabled(False)
        self.sftp_save_btn.clicked.connect(self._save_sftp_file)
        save_row.addWidget(self.sftp_save_btn)
        editor_layout.addLayout(save_row)
        splitter.addWidget(editor_wrapper)
        splitter.setSizes([200, 300])

        return wrapper

    def _build_settings_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        if not self.config.sftp_host:
            note = QLabel(
                "Editing serverconfig.xml needs SFTP (not configured for this server)."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(note)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(f"<tt>{SERVER_CONFIG_PATH}</tt>"))
        top_row.addStretch(1)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._load_server_settings)
        top_row.addWidget(reload_btn)
        layout.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.settings_form_widget = QWidget()
        self.settings_form = QFormLayout(self.settings_form_widget)
        self.settings_form_widget.setLayout(self.settings_form)
        scroll.setWidget(self.settings_form_widget)
        layout.addWidget(scroll, 1)

        bottom_row = QHBoxLayout()
        self.settings_hint = QLabel("")
        self.settings_hint.setStyleSheet("color: palette(placeholder-text);")
        bottom_row.addWidget(self.settings_hint, 1)
        self.settings_save_btn = QPushButton("Save")
        self.settings_save_btn.setEnabled(False)
        self.settings_save_btn.clicked.connect(self._save_server_settings)
        bottom_row.addWidget(self.settings_save_btn)
        layout.addLayout(bottom_row)

        return wrapper

    def _build_mods_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        if not self.config.sftp_host:
            note = QLabel("Managing mods requires SFTP (not configured for this server).")
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(note)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(f"Folder: <tt>{self.config.mods_dir}</tt>"))
        top_row.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._mods_refresh)
        top_row.addWidget(refresh_btn)
        layout.addLayout(top_row)

        self.mods_list = QListWidget()
        self.mods_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mods_list.customContextMenuRequested.connect(self._show_mods_menu)
        layout.addWidget(self.mods_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Mod Folder...")
        add_btn.clicked.connect(self._mods_add)
        btn_row.addWidget(add_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return wrapper

    def _build_console_panel(self) -> QWidget:
        tabs = QTabWidget()
        has_ptero = bool(self.config.pterodactyl_host)

        self.console_view, self.console_input = self._add_console_tab(
            tabs,
            "Console",
            note=None if has_ptero else "Live console needs Pterodactyl (not configured for this server).",
            view_placeholder="" if has_ptero else "(no live console -- connect Pterodactyl to see server output here)",
            input_placeholder="Type a server command and press Enter",
            on_send=self._send_console_command,
        )
        self.chat_view, self.chat_input = self._add_console_tab(
            tabs,
            "Chat",
            note=None if has_ptero else "Live chat needs Pterodactyl -- messages you send still go out via telnet's 'say'.",
            view_placeholder="" if has_ptero else "(no live chat -- connect Pterodactyl to see chat here)",
            input_placeholder="Type a chat message and press Enter",
            on_send=self._send_chat_message,
        )
        return tabs

    def _add_console_tab(
        self,
        tabs: QTabWidget,
        title: str,
        *,
        note: str | None,
        view_placeholder: str,
        input_placeholder: str,
        on_send: Callable[[], None],
    ) -> tuple[QPlainTextEdit, QLineEdit]:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(label)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setMaximumBlockCount(5000)
        view.setPlaceholderText(view_placeholder)
        layout.addWidget(view, 1)

        send_row = QHBoxLayout()
        line_edit = QLineEdit()
        line_edit.setPlaceholderText(input_placeholder)
        line_edit.returnPressed.connect(on_send)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(on_send)
        send_row.addWidget(line_edit, 1)
        send_row.addWidget(send_btn)
        layout.addLayout(send_row)

        tabs.addTab(wrapper, title)
        return view, line_edit

    # -- backend connections -----------------------------------------------------

    def _connect_backends(self) -> None:
        if self.config.telnet_host:
            self._run_async("telnet_connect", self._connect_telnet)
        if self.config.pterodactyl_host and self.config.pterodactyl_api_key and self.config.pterodactyl_server_id:
            self._run_async("ptero_connect", self._connect_pterodactyl)
        else:
            self.statusBar().showMessage(
                "No Pterodactyl connection configured -- power controls and live console/chat are disabled", 8000
            )
        if self.config.sftp_host and self.config.sftp_username:
            self._run_async("sftp_connect", self._connect_sftp)

    def _connect_telnet(self):
        client = TelnetClient(
            self.config.telnet_host,
            self.config.telnet_port,
            self.config.telnet_password,
            on_disconnect=self._handle_telnet_disconnected,
        )
        client.connect()
        return client

    def _connect_pterodactyl(self):
        client = PterodactylClient(
            self.config.pterodactyl_base_url,
            self.config.pterodactyl_api_key,
            self.config.pterodactyl_server_id,
        )
        return client

    def _connect_sftp(self):
        client = SftpClient(
            self.config.sftp_host,
            self.config.sftp_port,
            self.config.sftp_username,
            self.config.sftp_password,
        )
        client.connect()
        return client

    # -- telnet status / reconnection ---------------------------------------------

    TELNET_RECONNECT_INTERVAL_MS = 10_000

    def _set_telnet_status(self, connected: bool) -> None:
        if self._telnet_status_dot is not None:
            self._paint_status_dot(self._telnet_status_dot, connected, "Telnet")

    def _set_sftp_status(self, connected: bool) -> None:
        if self._sftp_status_dot is not None:
            self._paint_status_dot(self._sftp_status_dot, connected, "SFTP")

    def _handle_telnet_disconnected(self) -> None:
        # Called from the telnet reader thread -- marshal to the UI thread.
        self._bridge.result.emit("telnet_disconnected", None)

    def _schedule_telnet_reconnect(self) -> None:
        if self._telnet_reconnect_timer is not None or not self.config.telnet_host:
            return
        self._telnet_reconnect_timer = QTimer(self)
        self._telnet_reconnect_timer.setSingleShot(True)
        self._telnet_reconnect_timer.timeout.connect(self._attempt_telnet_reconnect)
        self._telnet_reconnect_timer.start(self.TELNET_RECONNECT_INTERVAL_MS)

    def _attempt_telnet_reconnect(self) -> None:
        self._telnet_reconnect_timer = None
        if self._telnet is not None:
            return
        self.statusBar().showMessage("Reconnecting to telnet...", 4000)
        self._run_async("telnet_reconnect", self._connect_telnet)

    # -- sftp status / reconnection ------------------------------------------------

    SFTP_RECONNECT_INTERVAL_MS = 10_000
    SFTP_HEALTH_CHECK_INTERVAL_MS = 15_000

    def _start_sftp_health_check(self) -> None:
        if self._sftp_health_timer is not None:
            return
        self._sftp_health_timer = QTimer(self)
        self._sftp_health_timer.timeout.connect(self._check_sftp_connection)
        self._sftp_health_timer.start(self.SFTP_HEALTH_CHECK_INTERVAL_MS)

    def _check_sftp_connection(self) -> None:
        if self._sftp is not None and not self._sftp.is_connected():
            self._handle_sftp_disconnected()

    def _handle_sftp_disconnected(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        self._set_sftp_status(False)
        self.statusBar().showMessage("SFTP connection lost -- will retry...", 4000)
        self._schedule_sftp_reconnect()

    def _schedule_sftp_reconnect(self) -> None:
        if self._sftp_reconnect_timer is not None or not self.config.sftp_host:
            return
        self._sftp_reconnect_timer = QTimer(self)
        self._sftp_reconnect_timer.setSingleShot(True)
        self._sftp_reconnect_timer.timeout.connect(self._attempt_sftp_reconnect)
        self._sftp_reconnect_timer.start(self.SFTP_RECONNECT_INTERVAL_MS)

    def _attempt_sftp_reconnect(self) -> None:
        self._sftp_reconnect_timer = None
        if self._sftp is not None:
            return
        self.statusBar().showMessage("Reconnecting to SFTP...", 4000)
        self._run_async("sftp_reconnect", self._connect_sftp)

    # -- async helper -------------------------------------------------------------

    def _run_async(self, tag: str, fn: Callable[[], object]) -> None:
        def task():
            try:
                value = fn()
            except Exception as exc:  # noqa: BLE001 -- surfaced to the UI as a message
                self._bridge.error.emit(tag, str(exc))
            else:
                self._bridge.result.emit(tag, value)

        threading.Thread(target=task, daemon=True).start()

    def _on_async_result(self, tag: str, value: object) -> None:
        if tag in ("telnet_connect", "telnet_reconnect"):
            self._telnet = value  # type: ignore[assignment]
            self._set_telnet_status(True)
            self.statusBar().showMessage("Telnet connected", 5000)
            self.refresh_players()
            if tag == "telnet_reconnect" and self._sftp is not None:
                self._load_server_settings()
        elif tag == "telnet_disconnected":
            self._telnet = None
            self._set_telnet_status(False)
            self.statusBar().showMessage("Telnet connection lost -- reconnecting...", 6000)
            self._schedule_telnet_reconnect()
        elif tag == "ptero_connect":
            self._ptero = value  # type: ignore[assignment]
            self.statusBar().showMessage("Pterodactyl connected", 5000)
            self._start_console_stream()
        elif tag == "list_players":
            self._players = value  # type: ignore[assignment]
            self._update_known_players()
            self._populate_player_table()
        elif tag == "ban_list":
            self._populate_ban_table(value)  # type: ignore[arg-type]
        elif tag == "teleport":
            reply = " / ".join(line.strip() for line in value if line.strip())  # type: ignore[union-attr]
            self.statusBar().showMessage(reply or "Teleport command sent", 6000)
        elif tag == "spawn_entity":
            reply = " / ".join(line.strip() for line in value if line.strip())  # type: ignore[union-attr]
            self.statusBar().showMessage(reply or "Spawn command sent", 6000)
        elif tag in ("admin_add", "admin_remove"):
            reply = " / ".join(line.strip() for line in value if line.strip())  # type: ignore[union-attr]
            self.statusBar().showMessage(reply or f"{tag} OK", 6000)
        elif tag in ("kick", "ban_add", "ban_remove", "console_command", "chat_message"):
            self.statusBar().showMessage(f"{tag} OK", 4000)
            if tag in ("ban_add", "ban_remove"):
                self.refresh_bans()
            if tag == "kick":
                self.refresh_players()
        elif tag == "game_time":
            if self._game_time_label is not None and value:
                self._game_time_label.setText(str(value))
        elif tag == "save_world":
            self.statusBar().showMessage("World saved -- safe to restart", 6000)
        elif tag == "power_action":
            self.statusBar().showMessage(f"Power action '{value}' sent", 4000)
        elif tag == "console_line":
            line = str(value)
            match = _CHAT_LINE_RE.search(line)
            if match:
                self.chat_view.appendPlainText(match["text"])
            else:
                self.console_view.appendPlainText(line)
        elif tag == "sftp_connect":
            self._sftp = value  # type: ignore[assignment]
            self._set_sftp_status(True)
            self._start_sftp_health_check()
            self.statusBar().showMessage("SFTP connected", 5000)
            self._sftp_browse("/")
            self._load_server_settings()
            self._mods_refresh()
        elif tag == "sftp_reconnect":
            self._sftp = value  # type: ignore[assignment]
            self._set_sftp_status(True)
            self._start_sftp_health_check()
            self.statusBar().showMessage("SFTP reconnected", 5000)
            self._sftp_browse(self._sftp_cwd)
            self._mods_refresh()
        elif tag == "sftp_list":
            path, entries = value  # type: ignore[misc]
            self._sftp_cwd = path
            self._sftp_entries = entries
            self._populate_sftp_list()
        elif tag == "sftp_read":
            path, content = value  # type: ignore[misc]
            self._sftp_open_path = path
            self._sftp_loading = True
            self.sftp_editor.setPlainText(content)
            self._sftp_loading = False
            self._set_sftp_dirty(False)
        elif tag == "sftp_write":
            self.statusBar().showMessage(f"Saved {value}", 4000)
            self._set_sftp_dirty(False)
        elif tag == "sftp_delete":
            self.statusBar().showMessage(f"Deleted {value}", 4000)
            if self._sftp_open_path == value:
                self._sftp_open_path = None
                self._sftp_loading = True
                self.sftp_editor.clear()
                self._sftp_loading = False
                self._set_sftp_dirty(False)
                self.sftp_editor_label.setText("(no file open)")
            self._sftp_browse(self._sftp_cwd)
        elif tag == "sftp_rename":
            old_path, new_path = value  # type: ignore[misc]
            self.statusBar().showMessage(f"Renamed to {new_path}", 4000)
            if self._sftp_open_path == old_path:
                self._sftp_open_path = new_path
                self._set_sftp_dirty(self._sftp_dirty)
            self._sftp_browse(self._sftp_cwd)
        elif tag == "sftp_chmod":
            self.statusBar().showMessage(f"Updated permissions for {value}", 4000)
            self._sftp_browse(self._sftp_cwd)
        elif tag == "sftp_upload":
            refresh_dir, count = value  # type: ignore[misc]
            self.statusBar().showMessage(f"Uploaded {count} file(s)", 5000)
            if refresh_dir == self._sftp_cwd:
                self._sftp_browse(self._sftp_cwd)
        elif tag == "reset_stats":
            player_name, deleted = value  # type: ignore[misc]
            if deleted:
                self.statusBar().showMessage(
                    f"Reset {player_name}: deleted {len(deleted)} file(s)", 6000
                )
            else:
                self.statusBar().showMessage(
                    f"No save files found for {player_name} — nothing deleted", 8000
                )
        elif tag == "settings_load":
            xml_text = str(value)
            self._settings_xml = xml_text
            self._settings_properties = parse_properties(xml_text)
            self._populate_settings_form()
            self.statusBar().showMessage("Loaded serverconfig.xml", 4000)
        elif tag == "settings_save":
            self._settings_xml = str(value)
            self._settings_properties = parse_properties(self._settings_xml)
            self._populate_settings_form()
            self.statusBar().showMessage("Saved serverconfig.xml", 5000)
            QMessageBox.information(
                self,
                "Settings saved",
                "serverconfig.xml has been updated.\n\nThe server needs to be restarted for these changes to take effect.",
            )
        elif tag == "mods_list":
            self._mods_entries = value  # type: ignore[assignment]
            self._populate_mods_list()
        elif tag == "mods_upload":
            mod_name, count = value  # type: ignore[misc]
            self.statusBar().showMessage(
                f"Installed mod '{mod_name}' ({count} file(s) uploaded, permissions set to 755)", 6000
            )
            self._mods_refresh()
        elif tag == "mods_delete":
            self.statusBar().showMessage(f"Removed mod '{value}'", 5000)
            self._mods_refresh()

    def _on_async_error(self, tag: str, message: str) -> None:
        self.statusBar().showMessage(f"{tag} failed: {message}", 8000)
        if tag == "telnet_connect":
            self._set_telnet_status(False)
            QMessageBox.warning(self, "Connection error", f"Telnet connect failed:\n{message}")
            self._schedule_telnet_reconnect()
        elif tag == "telnet_reconnect":
            self._set_telnet_status(False)
            self._schedule_telnet_reconnect()
        elif tag == "ptero_connect":
            QMessageBox.warning(self, "Connection error", f"Ptero connect failed:\n{message}")
        elif tag == "sftp_connect":
            self._set_sftp_status(False)
            QMessageBox.warning(self, "Connection error", f"SFTP connect failed:\n{message}")
            self._schedule_sftp_reconnect()
        elif tag == "sftp_reconnect":
            self._set_sftp_status(False)
            self._schedule_sftp_reconnect()
        elif tag == "settings_load":
            self.settings_hint.setText(f"Failed to load {SERVER_CONFIG_PATH}: {message}")
        elif tag == "settings_save":
            self.settings_hint.setText(f"Failed to save {SERVER_CONFIG_PATH}: {message}")
        elif tag == "sftp_upload":
            QMessageBox.warning(self, "Upload error", f"Upload failed:\n{message}")
        elif tag == "sftp_chmod":
            QMessageBox.warning(self, "Permissions error", f"Could not change permissions:\n{message}")
        elif tag == "reset_stats":
            QMessageBox.warning(self, "Reset failed", f"Could not reset player stats:\n{message}")
        elif tag == "mods_list":
            self.statusBar().showMessage(f"Mods folder not found or inaccessible: {message}", 8000)
        elif tag == "mods_upload":
            QMessageBox.warning(self, "Upload failed", f"Could not install mod:\n{message}")
        elif tag == "mods_delete":
            QMessageBox.warning(self, "Remove failed", f"Could not remove mod:\n{message}")

    # -- players ------------------------------------------------------------------

    PLAYER_REFRESH_INTERVAL_MS = 60_000

    def refresh_players(self) -> None:
        if not self._telnet:
            return
        self._run_async("list_players", self._telnet.list_players)
        self._run_async("game_time", self._telnet.get_game_time)

    def _populate_player_table(self) -> None:
        table = self.player_table
        online_steamids = {p.steamid for p in self._players}
        offline = sorted(
            [info for sid, info in self._known_players.items() if sid not in online_steamids],
            key=lambda i: i.get("last_seen", ""),
            reverse=True,
        )
        table.setRowCount(len(self._players) + len(offline))
        row = 0
        for player in self._players:
            name_item = QTableWidgetItem(player.name)
            name_item.setData(Qt.ItemDataRole.UserRole, player.steamid)
            table.setItem(row, 0, name_item)
            status_item = QTableWidgetItem("Online")
            status_item.setForeground(QColor("#2ecc71"))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 1, status_item)
            lvl_item = QTableWidgetItem(str(player.level))
            lvl_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 2, lvl_item)
            table.setItem(row, 3, QTableWidgetItem(player.steamid))
            table.setItem(row, 4, QTableWidgetItem(f"{player.x:.0f}, {player.y:.0f}, {player.z:.0f}"))
            ping_item = QTableWidgetItem(str(player.ping))
            ping_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 5, ping_item)
            table.setItem(row, 6, QTableWidgetItem(""))
            row += 1
        for info in offline:
            fg = QColor("#888888")

            def _gray(text: str, center: bool = False) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setForeground(fg)
                if center:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return it

            name_item = _gray(info.get("name", ""))
            name_item.setData(Qt.ItemDataRole.UserRole, info.get("steamid", ""))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, _gray("Offline", center=True))
            table.setItem(row, 2, _gray(str(info.get("level", "-")), center=True))
            table.setItem(row, 3, _gray(info.get("steamid", "")))
            table.setItem(row, 4, _gray("-"))
            table.setItem(row, 5, _gray("-", center=True))
            table.setItem(row, 6, _gray(info.get("last_seen", "")))
            row += 1
        self._check_level_cheaters()

    def _check_level_cheaters(self) -> None:
        import time as _time
        now = _time.monotonic()
        import math as _math
        for player in self._players:
            sid = player.steamid

            # --- level cheat check ---
            if sid not in self._level_history:
                self._level_history[sid] = (player.level, now)
            else:
                first_level, first_time = self._level_history[sid]
                elapsed_min = (now - first_time) / 60.0
                gained = player.level - first_level
                if elapsed_min >= 0.25 and gained > 0:
                    rate = gained / elapsed_min
                    if self.autoban_level_enabled.currentIndex() == 1 and rate > self.autoban_level_threshold.value():
                        self._autoban_cheater(player, f"Speed leveling: +{gained} lvl in {rate:.1f} lvl/min")
                        continue

            # --- speed hack check ---
            if sid not in self._position_history:
                self._position_history[sid] = (player.x, player.y, player.z, now)
            else:
                px, py, pz, pt = self._position_history[sid]
                elapsed_sec = now - pt
                if elapsed_sec >= 1.0:
                    dist = _math.sqrt((player.x - px) ** 2 + (player.y - py) ** 2 + (player.z - pz) ** 2)
                    speed = dist / elapsed_sec
                    if self.autoban_speed_enabled.currentIndex() == 1 and speed > self.autoban_speed_threshold.value():
                        self._autoban_cheater(player, f"Speed hack: {speed:.0f} m/s ({dist:.0f} m in {elapsed_sec:.0f}s)")
                        continue
                self._position_history[sid] = (player.x, player.y, player.z, now)

        # remove history for players who left
        online = {p.steamid for p in self._players}
        for sid in list(self._level_history):
            if sid not in online:
                del self._level_history[sid]
        for sid in list(self._position_history):
            if sid not in online:
                del self._position_history[sid]

    def _autoban_cheater(self, player: Player, reason: str) -> None:
        if not self._telnet:
            return
        self.statusBar().showMessage(f"Auto-banning {player.name}: {reason}", 10000)
        self.console_view.appendPlainText(f"[AUTO-BAN] {player.name} ({player.steamid}): {reason}")
        self._run_async("ban_add", lambda: self._telnet.ban_add(player.steamid, reason=reason))
        self._level_history.pop(player.steamid, None)
        self._position_history.pop(player.steamid, None)

    def _save_autoban_settings(self, *_args) -> None:
        self.config.autoban_level_enabled = self.autoban_level_enabled.currentIndex() == 1
        self.config.autoban_level_threshold = self.autoban_level_threshold.value()
        self.config.autoban_speed_enabled = self.autoban_speed_enabled.currentIndex() == 1
        self.config.autoban_speed_threshold = self.autoban_speed_threshold.value()
        if self._save_config_callback:
            self._save_config_callback(self.config)

    def _show_player_menu(self, pos) -> None:
        row = self.player_table.rowAt(pos.y())
        item = self.player_table.item(row, 0)
        if row < 0 or item is None:
            return
        steamid = item.data(Qt.ItemDataRole.UserRole)
        player = next((p for p in self._players if p.steamid == steamid), None)
        menu = QMenu(self)

        if player is not None:
            teleport_menu = menu.addMenu(f"Teleport '{player.name}' to")
            others = [p for p in self._players if p.entity_id != player.entity_id]
            if others:
                for other in others:
                    action = teleport_menu.addAction(other.name)
                    action.triggered.connect(
                        lambda _checked=False, src=player, dst=other.name: self._teleport_to_player(src, dst)
                    )
                teleport_menu.addSeparator()
            else:
                teleport_menu.addAction("(no other players online)").setEnabled(False)
            coords_action = teleport_menu.addAction("Coordinates...")
            coords_action.triggered.connect(lambda: self._teleport_to_coords_dialog(player))

            if player.steamid in self._teleport_history:
                prev = self._teleport_history[player.steamid]
                return_action = menu.addAction(
                    f"Return '{player.name}' to previous location  ({prev[0]:.0f}, {prev[1]:.0f}, {prev[2]:.0f})"
                )
                return_action.triggered.connect(lambda: self._return_player(player))

            menu.addSeparator()
            spawn_menu = menu.addMenu(f"Spawn at '{player.name}'")
            zombie_menu = spawn_menu.addMenu("Zombie")
            for label, entity_name in _ZOMBIE_TYPES:
                action = zombie_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, p=player, e=entity_name: self._spawn_entity(p, e)
                )
            zombie_menu.addSeparator()
            zombie_custom = zombie_menu.addAction("Custom...")
            zombie_custom.triggered.connect(lambda _checked=False, p=player: self._spawn_entity_dialog(p))

            animal_menu = spawn_menu.addMenu("Animal")
            for label, entity_name in _ANIMAL_TYPES:
                action = animal_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, p=player, e=entity_name: self._spawn_entity(p, e)
                )
            animal_menu.addSeparator()
            animal_custom = animal_menu.addAction("Custom...")
            animal_custom.triggered.connect(lambda _checked=False, p=player: self._spawn_entity_dialog(p))

            menu.addSeparator()
            kick_action = menu.addAction("Kick")
            kick_action.triggered.connect(lambda: self._kick_player(player))
            ban_action = menu.addAction("Ban...")
            ban_action.triggered.connect(lambda: self._ban_dialog_for(player.steamid or player.name))
            copy_action = menu.addAction("Copy SteamID")
            copy_action.triggered.connect(lambda: self._copy_to_clipboard(player.steamid))
            if player.steamid:
                steam_action = menu.addAction("Open Steam Profile")
                steam_action.triggered.connect(lambda: self._open_steam_profile(player.steamid))
            menu.addSeparator()
            grant_admin_action = menu.addAction("Grant Admin (Level 0)")
            grant_admin_action.triggered.connect(lambda: self._admin_grant(player.name, player.name))
            revoke_admin_action = menu.addAction("Revoke Admin")
            revoke_admin_action.triggered.connect(lambda: self._admin_revoke(player.name, player.name))
            menu.addSeparator()
            reset_action = menu.addAction("Reset Player Stats...")
            reset_action.triggered.connect(lambda: self._reset_player_stats(player.name, player.steamid, online=True))
        else:
            name = self._known_players.get(steamid, {}).get("name", steamid)
            menu.addAction(f"'{name}' is offline").setEnabled(False)
            menu.addSeparator()
            ban_action = menu.addAction("Ban...")
            ban_action.triggered.connect(lambda: self._ban_dialog_for(steamid or name))
            copy_action = menu.addAction("Copy SteamID")
            copy_action.triggered.connect(lambda: self._copy_to_clipboard(steamid))
            if steamid:
                steam_action = menu.addAction("Open Steam Profile")
                steam_action.triggered.connect(lambda: self._open_steam_profile(steamid))
            if steamid:
                menu.addSeparator()
                grant_admin_action = menu.addAction("Grant Admin (Level 0)")
                grant_admin_action.triggered.connect(
                    lambda _checked=False, sid=steamid, n=name: self._admin_grant(f"Steam_{sid}", n)
                )
                revoke_admin_action = menu.addAction("Revoke Admin")
                revoke_admin_action.triggered.connect(
                    lambda _checked=False, sid=steamid, n=name: self._admin_revoke(f"Steam_{sid}", n)
                )
            menu.addSeparator()
            reset_action = menu.addAction("Reset Player Stats...")
            reset_action.triggered.connect(lambda: self._reset_player_stats(name, steamid, online=False))
            remove_action = menu.addAction("Remove from history")
            remove_action.triggered.connect(lambda: self._remove_known_player(steamid))

        menu.exec(self.player_table.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Copied '{text}' to clipboard", 3000)

    def _open_steam_profile(self, steamid: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/profiles/{steamid}"))

    def _open_donate(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://cash.app/$j71rivera"))

    def _open_suggestions(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("mailto:j71rivera@gmail.com?subject=SJPSM Suggestion"))

    def _teleport_to_player(self, source: Player, target: str) -> None:
        if not self._telnet:
            return
        self._teleport_history[source.steamid] = (source.x, source.y, source.z)
        name = source.name
        self._run_async("teleport", lambda: self._telnet.teleport_to_player(name, target))

    def _teleport_to_coords_dialog(self, player: Player) -> None:
        dialog = TeleportToCoordsDialog(player.name, self)
        dialog.x.setValue(player.x)
        dialog.y.setValue(player.y)
        dialog.z.setValue(player.z)
        if dialog.exec() == QDialog.DialogCode.Accepted and self._telnet:
            x, y, z = dialog.coords()
            self._teleport_history[player.steamid] = (player.x, player.y, player.z)
            self._run_async("teleport", lambda: self._telnet.teleport_to_coords(player.name, x, y, z))

    def _return_player(self, player: Player) -> None:
        if not self._telnet:
            self.statusBar().showMessage("Return failed: telnet not connected", 5000)
            return
        if player.steamid not in self._teleport_history:
            self.statusBar().showMessage(
                f"Return failed: no saved position for {player.name} (steamid={player.steamid!r}, "
                f"history keys={list(self._teleport_history.keys())})", 10000
            )
            return
        x, y, z = self._teleport_history.pop(player.steamid)
        self.statusBar().showMessage(f"Returning {player.name} to ({x:.0f}, {y:.0f}, {z:.0f})...", 5000)
        self._run_async("teleport", lambda: self._telnet.teleport_to_coords(player.name, x, y, z))

    def _spawn_entity(self, player: Player, entity_name: str, count: int = 1) -> None:
        if not self._telnet:
            return
        entity_id = player.entity_id
        self.statusBar().showMessage(f"Spawning {count}x '{entity_name}' at {player.name}...", 4000)
        self._run_async("spawn_entity", lambda: self._telnet.spawn_entity(entity_id, entity_name, count))

    def _spawn_entity_dialog(self, player: Player) -> None:
        dialog = SpawnEntityDialog(player.name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            entity_name, count = dialog.values()
            if entity_name:
                self._spawn_entity(player, entity_name, count)

    def _admin_grant(self, identifier: str, display_name: str) -> None:
        if not self._telnet:
            return
        if QMessageBox.question(
            self, "Grant Admin", f"Grant level 0 (full admin) to '{display_name}'?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run_async("admin_add", lambda: self._telnet.admin_add(identifier, 0, display_name))

    def _admin_revoke(self, identifier: str, display_name: str) -> None:
        if not self._telnet:
            return
        if QMessageBox.question(
            self, "Revoke Admin", f"Revoke admin permissions from '{display_name}'?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run_async("admin_remove", lambda: self._telnet.admin_remove(identifier))

    def _kick_player(self, player: Player) -> None:
        if not self._telnet:
            return
        reason, ok = QInputDialog.getText(self, f"Kick {player.name}", "Reason (optional):")
        if not ok:
            return
        self._run_async("kick", lambda: self._telnet.kick(player.name, reason.strip()))

    def _update_known_players(self) -> None:
        if not self._players:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        changed = False
        for player in self._players:
            if not player.steamid:
                continue
            entry = {
                "steamid": player.steamid,
                "name": player.name,
                "level": player.level,
                "ip": player.ip,
                "last_seen": now,
            }
            if self._known_players.get(player.steamid) != entry:
                self._known_players[player.steamid] = entry
                changed = True
        if changed:
            self._save_known_players()

    def _save_known_players(self) -> None:
        self.config.known_players = list(self._known_players.values())
        if self._save_config_callback:
            self._save_config_callback(self.config)

    def _clear_player_history(self) -> None:
        if QMessageBox.question(
            self, "Clear player history",
            "Remove all players from the history list?\n\nCurrently online players will reappear immediately.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._known_players.clear()
        self._save_known_players()
        self._populate_player_table()
        if self._telnet:
            self.refresh_players()

    def _remove_known_player(self, steamid: str) -> None:
        self._known_players.pop(steamid, None)
        self._save_known_players()
        self._populate_player_table()

    def _get_xml_setting(self, name: str) -> str:
        for prop in self._settings_properties:
            if prop.name.lower() == name.lower():
                return prop.value
        return ""

    def _reset_player_stats(self, name: str, steamid: str, *, online: bool) -> None:
        if not self._sftp:
            QMessageBox.warning(self, "SFTP not connected", "SFTP must be connected to reset player stats.")
            return
        game_world = self._get_xml_setting("GameWorld") or "Navezgane"
        game_name = self._get_xml_setting("GameName") or "My Game"
        msg = (
            f"Delete all save data for '{name}' (SteamID: {steamid})?\n\n"
            f"GameWorld: {game_world}\nGameName: {game_name}\n\n"
            "This removes their character, inventory, and map data.\n"
        )
        if online:
            msg += "\nThe player is currently online — kick them first for the reset to take effect cleanly."
        if QMessageBox.question(self, "Reset player stats — are you sure?", msg) != QMessageBox.StandardButton.Yes:
            return
        self.statusBar().showMessage(f"Resetting stats for {name}...", 6000)
        self._run_async(
            "reset_stats",
            lambda: (name, self._sftp.delete_player_data(steamid, game_world, game_name)),
        )

    # -- bans ---------------------------------------------------------------------

    def refresh_bans(self) -> None:
        if not self._telnet:
            return
        self._run_async("ban_list", self._telnet.ban_list)

    def _populate_ban_table(self, entries: list[BanEntry]) -> None:
        table = self.ban_table
        table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            table.setItem(row, 0, QTableWidgetItem(entry.identifier))
            table.setItem(row, 1, QTableWidgetItem(entry.expires or entry.raw))
        table.resizeColumnsToContents()
        self._left_tabs.setTabText(1, f"Banned ({len(entries)})")

    def _ban_dialog_for(self, identifier: str) -> None:
        dialog = BanDialog(identifier, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and self._telnet:
            ident, duration, unit, reason = dialog.values()
            if not ident:
                return
            self._run_async("ban_add", lambda: self._telnet.ban_add(ident, duration, unit, reason))

    def _add_ban_dialog(self) -> None:
        self._ban_dialog_for("")

    def _remove_selected_ban(self) -> None:
        row = self.ban_table.currentRow()
        if row < 0 or not self._telnet:
            return
        identifier = self.ban_table.item(row, 0).text()
        if QMessageBox.question(self, "Remove ban", f"Remove ban for '{identifier}'?") != QMessageBox.StandardButton.Yes:
            return
        self._run_async("ban_remove", lambda: self._telnet.ban_remove(identifier))

    def _show_ban_menu(self, pos) -> None:
        row = self.ban_table.rowAt(pos.y())
        if row < 0:
            return
        identifier = self.ban_table.item(row, 0).text()
        menu = QMenu(self)
        unban_action = menu.addAction(f"Unban '{identifier}'")
        unban_action.triggered.connect(lambda: self._unban(identifier))
        copy_action = menu.addAction("Copy identifier")
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(identifier))
        menu.exec(self.ban_table.viewport().mapToGlobal(pos))

    def _unban(self, identifier: str) -> None:
        if not self._telnet:
            return
        self._run_async("ban_remove", lambda: self._telnet.ban_remove(identifier))

    # -- console / chat -----------------------------------------------------------

    def _start_console_stream(self) -> None:
        if not self._ptero:
            return
        self._console = ConsoleStream(self._ptero, on_line=self._append_console_line)
        self._console.start()

    def _append_console_line(self, line: str) -> None:
        # Called from the websocket thread -- marshal to the UI thread.
        self._bridge.result.emit("console_line", line)

    def _send_console_command(self) -> None:
        command = self.console_input.text().strip()
        if not command:
            return
        self.console_input.clear()
        self._send_to_console(command, tag="console_command")

    def _send_chat_message(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()
        self._send_to_console(f'say "Server Admin: {text}"', tag="chat_message")

    def _send_to_console(self, command: str, *, tag: str) -> None:
        if self._console is not None:
            try:
                self._console.send_command(command)
            except PterodactylError as exc:
                self.statusBar().showMessage(str(exc), 6000)
        elif self._telnet is not None:
            self._run_async(tag, lambda: self._telnet.run_command(command))
        else:
            self.statusBar().showMessage("No console connection available", 4000)

    # -- files / sftp ---------------------------------------------------------------

    @staticmethod
    def _sftp_join(base: str, name: str) -> str:
        if base in ("", "/"):
            return f"/{name}"
        return f"{base.rstrip('/')}/{name}"

    def _sftp_browse(self, path: str) -> None:
        if not self._sftp:
            self.statusBar().showMessage("SFTP is not connected", 4000)
            return
        self._run_async("sftp_list", lambda: self._do_sftp_list(path))

    def _sftp_navigate_to_typed_path(self) -> None:
        path = self.sftp_path_edit.text().strip()
        if not path.startswith("/"):
            path = f"/{path}"
        if path != "/":
            path = path.rstrip("/")
        self._sftp_browse(path)

    def _sftp_go_up(self) -> None:
        if self._sftp_cwd in ("", "/"):
            return
        parent = self._sftp_cwd.rsplit("/", 1)[0] or "/"
        self._sftp_browse(parent)

    def _sftp_entry_activated(self, item: QListWidgetItem) -> None:
        entry: FileEntry = item.data(Qt.ItemDataRole.UserRole)
        if entry.name == "..":
            self._sftp_go_up()
        elif entry.is_dir:
            self._sftp_browse(self._sftp_join(self._sftp_cwd, entry.name))
        else:
            self._open_sftp_file(self._sftp_join(self._sftp_cwd, entry.name))

    def _populate_sftp_list(self) -> None:
        self.sftp_path_edit.setText(self._sftp_cwd)
        self.sftp_list.clear()
        if self._sftp_cwd not in ("", "/"):
            up_item = QListWidgetItem("..")
            up_item.setData(Qt.ItemDataRole.UserRole, FileEntry(name="..", is_dir=True, size=0))
            self.sftp_list.addItem(up_item)
        for entry in self._sftp_entries:
            label = f"{entry.name}/" if entry.is_dir else f"{entry.name}   ({entry.size:,} bytes)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.sftp_list.addItem(item)

    def _open_sftp_file(self, path: str) -> None:
        if not self._sftp:
            self.statusBar().showMessage("SFTP is not connected", 4000)
            return
        if self._sftp_dirty and path != self._sftp_open_path and not self._confirm_discard_sftp_changes():
            return
        self._run_async("sftp_read", lambda: self._do_sftp_read(path))

    def _confirm_discard_sftp_changes(self) -> bool:
        return QMessageBox.question(
            self,
            "Unsaved changes",
            f"Discard unsaved changes to '{self._sftp_open_path}'?",
        ) == QMessageBox.StandardButton.Yes

    def _on_sftp_editor_changed(self) -> None:
        if self._sftp_loading:
            return
        self._set_sftp_dirty(True)

    def _set_sftp_dirty(self, dirty: bool) -> None:
        self._sftp_dirty = dirty
        self.sftp_save_btn.setEnabled(dirty and self._sftp_open_path is not None)
        if self._sftp_open_path:
            self.sftp_editor_label.setText(f"{self._sftp_open_path}{' *' if dirty else ''}")
        else:
            self.sftp_editor_label.setText("(no file open)")

    def _save_sftp_file(self) -> None:
        if not self._sftp or not self._sftp_open_path:
            return
        path = self._sftp_open_path
        content = self.sftp_editor.toPlainText()
        self._run_async("sftp_write", lambda: self._do_sftp_write(path, content))

    def _show_sftp_menu(self, pos) -> None:
        item = self.sftp_list.itemAt(pos)
        if item is None:
            return
        entry: FileEntry = item.data(Qt.ItemDataRole.UserRole)
        if entry.name == "..":
            return
        path = self._sftp_join(self._sftp_cwd, entry.name)

        menu = QMenu(self)
        delete_action = menu.addAction("Delete")
        delete_action.triggered.connect(lambda: self._delete_sftp_entry(entry, path))
        rename_action = menu.addAction("Rename...")
        rename_action.triggered.connect(lambda: self._rename_sftp_entry(entry, path))
        chmod_label = "Set Permissions (recursive)..." if entry.is_dir else "Set Permissions..."
        chmod_action = menu.addAction(chmod_label)
        chmod_action.triggered.connect(lambda: self._chmod_sftp_entry(entry, path))
        menu.exec(self.sftp_list.viewport().mapToGlobal(pos))

    def _delete_sftp_entry(self, entry: FileEntry, path: str) -> None:
        if not self._sftp:
            return
        if entry.is_dir:
            self.statusBar().showMessage("Deleting directories isn't supported here -- remove their files individually", 6000)
            return
        if QMessageBox.question(self, "Confirm", f"Delete '{entry.name}'? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        self._run_async("sftp_delete", lambda: self._do_sftp_delete(path))

    def _rename_sftp_entry(self, entry: FileEntry, path: str) -> None:
        if not self._sftp:
            return
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", QLineEdit.EchoMode.Normal, entry.name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == entry.name:
            return
        new_path = self._sftp_join(self._sftp_cwd, new_name)
        self._run_async("sftp_rename", lambda: self._do_sftp_rename(path, new_path))

    def _chmod_sftp_entry(self, entry: FileEntry, path: str) -> None:
        if not self._sftp:
            return
        default_mode = "755" if entry.is_dir else "644"
        prompt = f"Octal permission mode for '{entry.name}'"
        if entry.is_dir:
            prompt += " (applied to this folder and everything inside it)"
        text, ok = QInputDialog.getText(self, "Set Permissions", prompt + ":", QLineEdit.EchoMode.Normal, default_mode)
        text = text.strip()
        if not ok or not text:
            return
        try:
            mode = int(text, 8)
            if not (0 <= mode <= 0o7777):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Invalid mode", "Enter an octal permission mode, e.g. 755 or 644.")
            return
        self._run_async("sftp_chmod", lambda: self._do_sftp_chmod(path, mode, entry.is_dir))

    def _upload_sftp_files(self) -> None:
        if not self._sftp:
            self.statusBar().showMessage("SFTP is not connected", 4000)
            return
        local_paths, _ = QFileDialog.getOpenFileNames(
            self, "Select files to upload", "", "All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not local_paths:
            return
        cwd = self._sftp_cwd
        self.statusBar().showMessage(f"Uploading {len(local_paths)} file(s)...", 4000)
        self._run_async("sftp_upload", lambda: self._do_sftp_upload_files(local_paths, cwd))

    def _upload_sftp_folder(self) -> None:
        if not self._sftp:
            self.statusBar().showMessage("SFTP is not connected", 4000)
            return
        local_dir = QFileDialog.getExistingDirectory(
            self, "Select folder to upload", "",
            options=QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        if not local_dir:
            return
        cwd = self._sftp_cwd
        remote_dir = self._sftp_join(cwd, os.path.basename(local_dir.rstrip("/\\")))
        self.statusBar().showMessage(f"Uploading folder '{os.path.basename(local_dir)}'...", 4000)
        self._run_async("sftp_upload", lambda: self._do_sftp_upload_dir(local_dir, remote_dir, cwd))

    # -- sftp worker-thread helpers (run off the UI thread via _run_async) -----------

    def _do_sftp_list(self, path: str):
        return path, self._sftp.list_dir(path)

    def _do_sftp_read(self, path: str):
        return path, self._sftp.read_file(path)

    def _do_sftp_write(self, path: str, content: str):
        self._sftp.write_file(path, content)
        return path

    def _do_sftp_delete(self, path: str):
        self._sftp.delete_file(path)
        return path

    def _do_sftp_rename(self, old_path: str, new_path: str):
        self._sftp.rename(old_path, new_path)
        return old_path, new_path

    def _do_sftp_chmod(self, path: str, mode: int, recursive: bool):
        if recursive:
            self._sftp.chmod_recursive(path, mode)
        else:
            self._sftp.chmod(path, mode)
        return path

    def _do_sftp_upload_files(self, local_paths: list[str], remote_dir: str):
        for local_path in local_paths:
            remote_path = self._sftp_join(remote_dir, os.path.basename(local_path))
            self._sftp.upload_file(local_path, remote_path)
        return remote_dir, len(local_paths)

    def _do_sftp_upload_dir(self, local_dir: str, remote_dir: str, refresh_dir: str):
        count = self._sftp.upload_dir(local_dir, remote_dir)
        return refresh_dir, count

    # -- mods -----------------------------------------------------------------------

    def _mods_refresh(self) -> None:
        if not self._sftp:
            return
        self._run_async("mods_list", self._do_mods_list)

    def _populate_mods_list(self) -> None:
        self.mods_list.clear()
        locked = set(self.config.locked_mods)
        for name in sorted(self._mods_entries, key=str.lower):
            is_locked = name in locked
            label = f"{name}  [protected]" if is_locked else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if is_locked:
                item.setForeground(QColor("#888888"))
                item.setToolTip("Protected — right-click to unlock")
            else:
                item.setToolTip("Right-click to remove or lock")
            self.mods_list.addItem(item)
        idx = self._left_tabs.indexOf(self._mods_panel)
        self._left_tabs.setTabText(idx, f"Mods ({len(self._mods_entries)})")

    def _mods_add(self) -> None:
        if not self._sftp:
            self.statusBar().showMessage("SFTP is not connected", 4000)
            return
        local_dir = QFileDialog.getExistingDirectory(
            self, "Select mod folder to upload", "",
            options=QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        if not local_dir:
            return
        mod_name = os.path.basename(local_dir.rstrip("/\\"))
        remote_dir = self._sftp_join(self.config.mods_dir, mod_name)
        self.statusBar().showMessage(f"Installing mod '{mod_name}'...", 4000)
        self._run_async("mods_upload", lambda: self._do_mods_upload(local_dir, remote_dir, mod_name))

    def _show_mods_menu(self, pos) -> None:
        item = self.mods_list.itemAt(pos)
        if item is None:
            return
        mod_name: str = item.data(Qt.ItemDataRole.UserRole)
        is_locked = mod_name in self.config.locked_mods
        menu = QMenu(self)
        if is_locked:
            unlock_action = menu.addAction("Unlock (allow removal)")
            unlock_action.triggered.connect(lambda: self._mods_unlock(mod_name))
        else:
            remove_action = menu.addAction(f"Remove '{mod_name}'...")
            remove_action.triggered.connect(lambda: self._mods_remove(mod_name))
            menu.addSeparator()
            lock_action = menu.addAction("Lock (prevent removal)")
            lock_action.triggered.connect(lambda: self._mods_lock(mod_name))
        menu.exec(self.mods_list.viewport().mapToGlobal(pos))

    def _mods_lock(self, mod_name: str) -> None:
        if mod_name not in self.config.locked_mods:
            self.config.locked_mods.append(mod_name)
            if self._save_config_callback:
                self._save_config_callback(self.config)
        self._populate_mods_list()

    def _mods_unlock(self, mod_name: str) -> None:
        if mod_name in self.config.locked_mods:
            self.config.locked_mods.remove(mod_name)
            if self._save_config_callback:
                self._save_config_callback(self.config)
        self._populate_mods_list()

    def _mods_remove(self, mod_name: str) -> None:
        if not self._sftp:
            return
        if QMessageBox.question(
            self,
            "Remove mod",
            f"Permanently delete mod '{mod_name}' from the server?\n\nThis cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run_async("mods_delete", lambda: self._do_mods_delete(mod_name))

    def _do_mods_list(self):
        entries = self._sftp.list_dir(self.config.mods_dir)
        return [e.name for e in entries if e.is_dir and not e.name.startswith(".")]

    def _do_mods_upload(self, local_dir: str, remote_dir: str, mod_name: str):
        count = self._sftp.upload_dir(local_dir, remote_dir)
        self._sftp.chmod_recursive(remote_dir, 0o755)
        return mod_name, count

    def _do_mods_delete(self, mod_name: str):
        self._sftp.delete_dir(self._sftp_join(self.config.mods_dir, mod_name))
        return mod_name

    # -- server settings (serverconfig.xml) ------------------------------------------

    def _load_server_settings(self) -> None:
        if not self._sftp:
            self.statusBar().showMessage("SFTP is not connected -- can't load serverconfig.xml", 4000)
            return
        if self._settings_dirty and not self._confirm_discard_settings_changes():
            return
        self._run_async("settings_load", lambda: self._sftp.read_file(SERVER_CONFIG_PATH))

    def _confirm_discard_settings_changes(self) -> bool:
        return QMessageBox.question(
            self,
            "Unsaved changes",
            "Discard unsaved changes to server settings?",
        ) == QMessageBox.StandardButton.Yes

    def _populate_settings_form(self) -> None:
        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        self._settings_widgets.clear()
        self._settings_loading = True
        try:
            for prop in self._settings_properties:
                widget = self._make_settings_widget(prop)
                self._settings_widgets[prop.name] = widget
                if prop.name in _STARTUP_OVERRIDES:
                    widget.setEnabled(False)
                    container = QWidget()
                    row_layout = QHBoxLayout(container)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.addWidget(widget)
                    note = QLabel("controlled by Pterodactyl startup variable")
                    note.setStyleSheet("color: palette(placeholder-text); font-style: italic;")
                    row_layout.addWidget(note)
                    row_layout.addStretch(1)
                    self.settings_form.addRow(prop.name, container)
                else:
                    self.settings_form.addRow(prop.name, widget)
        finally:
            self._settings_loading = False
        self._set_settings_dirty(False)

    def _make_settings_widget(self, prop: XmlProperty) -> QWidget:
        if prop.kind == "bool":
            widget = QComboBox()
            widget.addItems(["true", "false"])
            widget.setCurrentText(prop.value.lower())
            widget.currentTextChanged.connect(self._mark_settings_dirty)
            return widget
        if prop.kind == "int":
            widget = QSpinBox()
            widget.setRange(-2_000_000_000, 2_000_000_000)
            widget.setValue(int(prop.value))
            widget.valueChanged.connect(self._mark_settings_dirty)
            return widget
        if prop.kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-1_000_000_000.0, 1_000_000_000.0)
            decimals = len(prop.value.split(".", 1)[1]) if "." in prop.value else 1
            widget.setDecimals(max(decimals, 1))
            widget.setValue(float(prop.value))
            widget.valueChanged.connect(self._mark_settings_dirty)
            return widget
        widget = QLineEdit(prop.value)
        widget.textEdited.connect(self._mark_settings_dirty)
        return widget

    @staticmethod
    def _widget_value(prop: XmlProperty, widget: QWidget) -> str:
        if prop.kind == "bool":
            return widget.currentText()
        if prop.kind == "int":
            return str(widget.value())
        if prop.kind == "float":
            return f"{widget.value():.{widget.decimals()}f}"
        return widget.text()

    def _mark_settings_dirty(self, *_args) -> None:
        if self._settings_loading:
            return
        self._set_settings_dirty(True)

    def _set_settings_dirty(self, dirty: bool) -> None:
        self._settings_dirty = dirty
        self.settings_save_btn.setEnabled(dirty)
        self.settings_hint.setText("Unsaved changes -- restart the server after saving for them to take effect." if dirty else "")

    def _save_server_settings(self) -> None:
        if not self._sftp or self._settings_xml is None:
            return
        changes = {}
        for prop in self._settings_properties:
            widget = self._settings_widgets.get(prop.name)
            if widget is None:
                continue
            new_value = self._widget_value(prop, widget)
            if new_value != prop.value:
                changes[prop.name] = new_value
        if not changes:
            self.statusBar().showMessage("No changes to save", 4000)
            return
        xml_text = self._settings_xml
        self._run_async("settings_save", lambda: self._do_settings_save(xml_text, changes))

    def _do_settings_save(self, xml_text: str, changes: dict[str, str]):
        new_xml = apply_property_changes(xml_text, changes)
        self._sftp.write_file(SERVER_CONFIG_PATH, new_xml)
        return new_xml

    # -- power ---------------------------------------------------------------------

    def _send_power_action(self, action: str) -> None:
        if not self._ptero:
            self.statusBar().showMessage("Pterodactyl is not connected", 4000)
            return
        if action in ("stop", "kill") and QMessageBox.question(
            self, "Confirm", f"Are you sure you want to {action} the server?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run_async("power_action", lambda: (self._ptero.send_power_action(action), action)[1])

    def _save_world(self) -> None:
        if not self._telnet:
            self.statusBar().showMessage("Telnet is not connected", 4000)
            return
        self._run_async("save_world", lambda: self._telnet.run_command("saveworld"))
