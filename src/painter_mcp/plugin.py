"""Painter plugin lifecycle. Qt owns dispatch; socket threads own transport controls."""

from __future__ import annotations

import json
import os

from . import __version__
from .common import atomic_write, connection_path, dumps

_runtime = None


def start_plugin():
    global _runtime
    if _runtime is not None:
        return
    from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Slot

    from .adapter import PainterAdapter
    from .broker import Broker
    from .engine import Engine
    from .state import State
    from .transport import Transport

    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError(
            "Start Painter MCP from Painter's Python plugin loader on its application thread"
        )

    class Dispatch(QObject):
        def __init__(self, broker):
            super().__init__(app)
            self.broker = broker
            self.in_tick = False
            self.timer = QTimer(self)
            self.timer.setInterval(15)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        @Slot()
        def tick(self):
            if self.in_tick:
                return
            self.in_tick = True
            try:
                self.broker.pump()
            finally:
                self.in_tick = False

    state = State()
    adapter = PainterAdapter(state)
    broker = Broker(Engine(adapter, state))
    transport = None
    dispatch = None
    path = connection_path()
    try:
        if path.exists():
            from .client import Client
            from .common import Fault

            try:
                existing = Client(path).health()
            except Fault:
                existing = None
            if existing and not existing.get("closed"):
                raise RuntimeError(
                    "Another Painter MCP instance owns this connection file. Use a distinct PAINTER_MCP_CONNECTION for each instance."
                )
        transport = Transport(broker)
        dispatch = Dispatch(broker)
        transport.start()
        atomic_write(
            path,
            dumps(
                {
                    "version": __version__,
                    "host": "127.0.0.1",
                    "port": transport.port,
                    "token": transport.token,
                    "runtime_id": broker.runtime_id,
                    "pid": os.getpid(),
                }
            ),
            private=True,
        )
        _runtime = (adapter, transport, dispatch, path, broker.runtime_id)
        app.aboutToQuit.connect(close_plugin)
        adapter.sp.logging.info(
            f"Painter MCP {__version__} listening on loopback port {transport.port}"
        )
    except BaseException:
        if dispatch:
            dispatch.timer.stop()
            dispatch.deleteLater()
        if transport:
            transport.close()
        adapter.close()
        raise


def close_plugin():
    global _runtime
    if _runtime is None:
        return
    adapter, transport, dispatch, path, runtime_id = _runtime
    _runtime = None
    dispatch.timer.stop()
    try:
        dispatch.parent().aboutToQuit.disconnect(close_plugin)
    except (RuntimeError, TypeError):
        pass
    transport.close()
    adapter.close()
    dispatch.deleteLater()
    try:
        if json.loads(path.read_text(encoding="utf-8"))["runtime_id"] == runtime_id:
            path.unlink()
    except (OSError, ValueError, KeyError):
        pass
