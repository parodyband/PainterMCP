"""Painter Help-menu update command and automatic notifications, owned by Qt."""

import time
import webbrowser

from .updater import UpdateWorker


def attach(sp):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMessageBox

    class Controller:
        def __init__(self):
            self.worker = UpdateWorker(sp.application.version())
            self.window = sp.ui.get_main_window()
            self.action = QAction("Check Painter MCP Updates…", self.window)
            self.action.setObjectName("painter_mcp_check_updates")
            self.action.triggered.connect(self.check)
            sp.ui.add_action(sp.ui.ApplicationMenu.Help, self.action)
            self.timer = QTimer(self.window)
            self.timer.setInterval(250)
            self.timer.timeout.connect(self.poll)
            self.timer.start()
            self.last_auto = time.monotonic()
            self.worker.check()

        def check(self):
            if not self.worker.check(manual=True):
                QMessageBox.information(
                    self.window, "Painter MCP", "An update operation is already running."
                )

        def poll(self):
            if time.monotonic() - self.last_auto > 300:
                self.last_auto = time.monotonic()
                self.worker.check()
            if self.worker.result is None:
                return
            result, self.worker.result = self.worker.result, None
            state = result["state"]
            if state == "available":
                box = QMessageBox(self.window)
                box.setWindowTitle("Painter MCP update available")
                box.setText(
                    f"Painter MCP {result['version']} is available. Install it alongside the current version? Restart Painter and your AI client to activate it."
                )
                install = box.addButton("Install Update", QMessageBox.ButtonRole.AcceptRole)
                box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
                view = box.addButton("View Release", QMessageBox.ButtonRole.ActionRole)
                box.exec()
                if box.clickedButton() == install:
                    self.worker.install(result)
                elif box.clickedButton() == view:
                    webbrowser.open(result["release_url"])
            elif state == "staged":
                conflicts = any(not s.get("installed") for s in result.get("skills", {}).values())
                message = f"Painter MCP {result['version']} is staged. Restart Painter and your AI client to activate it."
                if conflicts:
                    message += (
                        " Modified companion skills were preserved; review their incoming files."
                    )
                QMessageBox.information(self.window, "Painter MCP", message)
            elif self.worker.manual:
                message = result.get("error") or {
                    "no_release": "No stable release has been published yet.",
                    "up_to_date": "Painter MCP is up to date.",
                    "incompatible": "The latest release does not support this Painter/platform.",
                }.get(state, state)
                QMessageBox.information(self.window, "Painter MCP", message)

        def close(self):
            self.timer.stop()
            self.timer.deleteLater()
            self.action.deleteLater()
            self.worker.close()

    return Controller()
