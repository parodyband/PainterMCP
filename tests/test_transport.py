import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from painter_mcp.client import Client, render_receipt
from painter_mcp.common import Fault
from painter_mcp.transport import Transport

from .conftest import pump_all


@pytest.fixture
def transport(broker, tmp_path):
    server = Transport(broker)
    server.start()
    path = tmp_path / "connection.json"
    path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": server.port,
                "token": server.token,
                "runtime_id": broker.runtime_id,
            }
        ),
        encoding="utf-8",
    )
    yield server, Client(path)
    server.close()


def test_real_loopback_auth_and_health(transport):
    server, client = transport
    assert client.health()["runtime_id"] == server.broker.runtime_id
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/health")
    assert error.value.code == 401
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/health",
        headers={"Authorization": "Bearer " + server.token, "Origin": "https://malicious.example"},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req)
    assert error.value.code == 403


def test_http_submit_status_duplicate_and_native_image(transport, adapter):
    server, client = transport
    rid = server.broker.runtime_id + ":http-edit"
    args = {
        "steps": [{"id": "a", "op": "layers.create", "args": {"kind": "fill"}}],
        "request_id": rid,
        "wait_ms": 0,
        "observe": {"image": "texture"},
    }
    receipt = client.submit("painter_run", args)
    assert receipt["state"] == "queued"
    pump_all(server.broker)
    result = render_receipt(client.status(rid))
    assert result["content"][1]["type"] == "image"
    assert "aGVsbG8=" not in json.dumps(result["structuredContent"])
    assert client.submit("painter_run", args)["state"] == "completed"
    assert adapter.value == 1


def test_transport_health_during_slow_application_call(transport, adapter):
    server, client = transport
    started = threading.Event()
    release = threading.Event()
    original = adapter.invoke

    def slow(name, args):
        started.set()
        release.wait(2)
        return original(name, args)

    adapter.invoke = slow
    client.submit("painter_run", {"steps": [{"id": "a", "op": "project.info"}], "wait_ms": 0})
    app_thread = threading.Thread(target=server.broker.pump)
    app_thread.start()
    assert started.wait(1)
    before = time.perf_counter()
    assert client.health()["active_request"]
    assert time.perf_counter() - before < 1
    release.set()
    app_thread.join(2)


def test_invalid_wait_rejected_before_queue(transport):
    server, client = transport
    with pytest.raises(Fault):
        client.request(
            "/submit",
            {
                "tool": "painter_observe",
                "args": {},
                "wait_ms": 6000,
                "runtime_id": server.broker.runtime_id,
            },
        )
    assert not server.broker.records


def test_unknown_outcome_includes_generated_request_id(transport):
    _, client = transport

    def broken(*args, **kwargs):
        raise Fault("CONNECTION_LOST", "Unknown outcome")

    client.request = broken
    with pytest.raises(Fault) as error:
        client.submit("painter_observe", {})
    assert error.value.details["request_id"]


def test_connection_rejects_remote_host(tmp_path):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"host": "example.com", "port": 80}), encoding="utf-8")
    with pytest.raises(Fault) as error:
        Client(path).load()
    assert error.value.code == "INVALID_CONNECTION"


def test_malformed_and_oversized_json(transport):
    server, _ = transport
    for body in (b"null", b"{broken", b'{"x":NaN}', b" " * 1_048_577):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/submit",
            data=body,
            headers={"Authorization": "Bearer " + server.token, "Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 400
