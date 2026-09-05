import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from painter_mcp import plugin

from .conftest import FakeAdapter


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)


class QObject:
    def __init__(self, parent=None):
        self.owner = parent
        self.deleted = False

    def parent(self):
        return self.owner

    def deleteLater(self):
        self.deleted = True


class QTimer(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.timeout = Signal()
        self.running = False

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def qt(monkeypatch, tmp_path):
    app_thread = object()
    app = SimpleNamespace(thread=lambda: app_thread, aboutToQuit=Signal())
    core = ModuleType("PySide6.QtCore")
    core.QCoreApplication = SimpleNamespace(instance=lambda: app)
    core.QThread = SimpleNamespace(currentThread=lambda: app_thread)
    core.QObject = QObject
    core.QTimer = QTimer
    core.Slot = lambda: lambda function: function
    monkeypatch.setitem(sys.modules, "PySide6", ModuleType("PySide6"))
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", core)
    monkeypatch.setenv("PAINTER_MCP_CONNECTION", str(tmp_path / "connection.json"))
    import painter_mcp.adapter
    import painter_mcp.updater_ui

    monkeypatch.setattr(painter_mcp.updater_ui, "attach", lambda sp: None)

    class Adapter(FakeAdapter):
        def __init__(self, state):
            super().__init__(state)
            self.sp = SimpleNamespace(logging=SimpleNamespace(info=lambda message: None))
            self.closed = False

        def close(self):
            self.closed = True
            self.state.reset_project()

    monkeypatch.setattr(painter_mcp.adapter, "PainterAdapter", Adapter)
    yield app, tmp_path / "connection.json"
    plugin.close_plugin()


def test_start_stop_restart_is_idempotent_and_cleans_every_resource(qt):
    app, path = qt
    plugin.start_plugin()
    adapter, transport, dispatch, _, _ = plugin._runtime
    first_id = json.loads(path.read_text())["runtime_id"]
    plugin.start_plugin()
    assert len(app.aboutToQuit.callbacks) == 1
    assert dispatch.timer.running
    plugin.close_plugin()
    plugin.close_plugin()
    assert adapter.closed
    assert not transport.thread.is_alive()
    assert not dispatch.timer.running
    assert dispatch.deleted
    assert not path.exists()
    assert not app.aboutToQuit.callbacks
    plugin.start_plugin()
    assert json.loads(path.read_text())["runtime_id"] != first_id


def test_stop_does_not_delete_another_instances_connection(qt):
    _, path = qt
    plugin.start_plugin()
    content = json.loads(path.read_text())
    content["runtime_id"] = "another-instance"
    path.write_text(json.dumps(content))
    plugin.close_plugin()
    assert path.exists()


def test_startup_file_failure_releases_listener_and_adapter(qt, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("Disk full")

    monkeypatch.setattr(plugin, "atomic_write", fail)
    with pytest.raises(OSError):
        plugin.start_plugin()
    assert plugin._runtime is None
