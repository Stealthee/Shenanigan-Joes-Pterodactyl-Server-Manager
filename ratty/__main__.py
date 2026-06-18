import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ratty import storage
from ratty.config import ServerConfig
from ratty.ui.main_window import MainWindow
from ratty.ui.server_list_dialog import ServerListDialog

# Held open for the lifetime of the process so the OS lock stays active.
_lock_fh = None


def _acquire_singleton_lock() -> bool:
    """Cross-platform single-instance guard via an exclusive lock on a temp file.

    fcntl (POSIX) and msvcrt (Windows) lock files in incompatible ways, so only
    the locking call itself branches on platform.
    """
    global _lock_fh
    lock_path = Path(tempfile.gettempdir()) / "sjpsm.lock"
    fh = open(lock_path, "w+")
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.write("locked")
            fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh = fh  # keep reference alive — releasing it drops the lock
        return True
    except OSError:
        fh.close()
        return False


def main() -> int:
    app = QApplication(sys.argv)
    # Let KDE / the task manager associate our window with SJPSM.desktop
    # so "Pin to Task Manager" works correctly.
    app.setApplicationName("sjpsm")
    app.setOrganizationName("ratty")
    app.setDesktopFileName("SJPSM")

    if not _acquire_singleton_lock():
        QMessageBox.information(
            None,
            "SJPSM already running",
            "Shenanigan Joe's Pterodactyl Server Manager is already open.",
        )
        return 0

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
