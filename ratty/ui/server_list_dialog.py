"""Startup dialog: pick a saved server, or add/edit/remove profiles."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ratty.config import ServerConfig
from ratty.ui.connection_dialog import ConnectionDialog


class ServerListDialog(QDialog):
    def __init__(self, servers: list[ServerConfig], parent=None):
        super().__init__(parent)
        self.setWindowTitle("SJPSM -- Select a Server")
        self.setMinimumSize(380, 320)
        self.servers = list(servers)
        self._chosen: ServerConfig | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Saved servers:"))

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._connect())
        layout.addWidget(self.list_widget, 1)
        self._reload_list()

        buttons = QHBoxLayout()
        connect_btn = QPushButton("Connect")
        connect_btn.setDefault(True)
        connect_btn.clicked.connect(self._connect)
        add_btn = QPushButton("Add...")
        add_btn.clicked.connect(self._add)
        edit_btn = QPushButton("Edit...")
        edit_btn.clicked.connect(self._edit)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove)
        for b in (connect_btn, add_btn, edit_btn, remove_btn):
            buttons.addWidget(b)
        layout.addLayout(buttons)

    # -- helpers ---------------------------------------------------------------

    def _reload_list(self, select_index: int | None = None) -> None:
        self.list_widget.clear()
        for server in self.servers:
            detail = server.telnet_host or server.pterodactyl_host or "(no connection info)"
            item = QListWidgetItem(f"{server.name}  --  {detail}")
            self.list_widget.addItem(item)
        if select_index is not None and 0 <= select_index < self.list_widget.count():
            self.list_widget.setCurrentRow(select_index)
        elif self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def chosen_config(self) -> ServerConfig | None:
        return self._chosen

    # -- actions ----------------------------------------------------------------

    def _connect(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            QMessageBox.information(self, "No server selected", "Add a server first, or select one from the list.")
            return
        self._chosen = self.servers[row]
        self.accept()

    def _add(self) -> None:
        dialog = ConnectionDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.servers.append(dialog.result_config())
            self._reload_list(select_index=len(self.servers) - 1)

    def _edit(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        dialog = ConnectionDialog(self.servers[row], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.servers[row] = dialog.result_config()
            self._reload_list(select_index=row)

    def _remove(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        server = self.servers[row]
        if QMessageBox.question(self, "Remove server", f"Remove '{server.name}' from the list?") != QMessageBox.StandardButton.Yes:
            return
        del self.servers[row]
        self._reload_list(select_index=min(row, len(self.servers) - 1))
