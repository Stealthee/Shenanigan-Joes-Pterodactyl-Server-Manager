import sys
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QDialog

from ratty import storage
from ratty.config import ServerConfig
from ratty.ui.main_window import MainWindow
from ratty.ui.server_list_dialog import ServerListDialog


def main() -> int:
    app = QApplication(sys.argv)

    servers = storage.load_servers()
    picker = ServerListDialog(servers)
    if picker.exec() != QDialog.DialogCode.Accepted:
        storage.save_servers(picker.servers)
        return 0

    storage.save_servers(picker.servers)
    config = picker.chosen_config()
    if config is None:
        return 0

    servers = picker.servers

    def save_config(updated: "ServerConfig") -> None:
        for i, s in enumerate(servers):
            if s.name == updated.name and s.telnet_host == updated.telnet_host:
                servers[i] = updated
                break
        storage.save_servers(servers)

    window = MainWindow(config, save_config_callback=save_config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
