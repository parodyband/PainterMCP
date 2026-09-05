import contextlib
import copy
import threading
from types import SimpleNamespace

import pytest

from painter_mcp.broker import Broker
from painter_mcp.catalog import OPS
from painter_mcp.common import Fault
from painter_mcp.engine import Engine
from painter_mcp.state import State


class FakeAdapter:
    """Deterministic application boundary; tests assert bridge contracts, not Adobe behavior."""

    def __init__(self, state):
        self.state = state
        self.calls = []
        self.value = 0
        self.is_busy = False
        self.scope_depth = 0
        self.thread_ids = []
        self.capture_error = False
        self.sp = SimpleNamespace()

    def capabilities(self):
        return {
            "painter_version": "test-double",
            "operations": {n: {"available": True} for n in OPS},
        }

    def busy(self):
        return self.is_busy

    def invoke(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        self.thread_ids.append(threading.get_ident())
        if name == "layers.delete":
            raise Fault("TEST_FAILURE", "An edit failed after earlier work")
        if name == "layers.create":
            self.value += 1
            return {"ref": self.state.ref("node", self.value), "name": args.get("name", "Created")}
        if name == "layers.update":
            self.value += 1
            return {"updated": args["node"]}
        if name == "project.close":
            self.state.reset_project()
            return {"open": False}
        if name == "project.metadata":
            return args.get("value")
        return {"value": self.value}

    def facts(self, args):
        return {
            "project": {"open": True, "epoch": self.state.epoch},
            "value": self.value,
            "scope": args.get("texture_set", "active"),
        }

    def capture(self, args):
        if self.capture_error:
            raise Fault("CAPTURE_FAILED", "No image")
        if args.get("image", "none") != "none":
            return [{"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}], {
                "kind": "fake"
            }
        return [], {"kind": "none"}

    @contextlib.contextmanager
    def undo_scope(self, name):
        self.scope_depth += 1
        try:
            yield
        finally:
            self.scope_depth -= 1

    def encode(self, value):
        return value


@pytest.fixture
def state():
    return State()


@pytest.fixture
def adapter(state):
    return FakeAdapter(state)


@pytest.fixture
def engine(adapter, state):
    return Engine(adapter, state)


@pytest.fixture
def broker(engine):
    instance = Broker(engine)
    yield instance
    instance.close()


def execute(engine, tool, args):
    iterator = engine.execute(tool, args)
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


def pump_all(broker, maximum=100):
    for _ in range(maximum):
        broker.pump()
        if broker.active is None and not broker.queue:
            return
    raise AssertionError("Broker did not become idle")
