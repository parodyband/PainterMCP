import threading

import pytest

from painter_mcp.broker import Broker
from painter_mcp.common import Fault

from .conftest import pump_all

ACTION = {"steps": [{"id": "edit", "op": "layers.create", "args": {"kind": "fill"}}]}


def submit(broker, args=None, suffix="edit"):
    return broker.submit(
        "painter_run", args or ACTION, broker.runtime_id + ":" + suffix, broker.runtime_id
    )


def test_queued_running_completed_and_replay(broker, adapter):
    receipt = submit(broker)
    assert receipt["state"] == "queued"
    broker.pump()
    assert broker.status(receipt["request_id"])["state"] == "running"
    pump_all(broker)
    assert broker.status(receipt["request_id"])["state"] == "completed"
    assert submit(broker)["state"] == "completed"
    pump_all(broker)
    assert adapter.value == 1


def test_conflicting_id_and_restart_epoch_rejected(broker):
    submit(broker)
    with pytest.raises(Fault) as error:
        submit(broker, {"steps": [{"id": "read", "op": "project.info"}]})
    assert error.value.code == "REQUEST_ID_CONFLICT"
    with pytest.raises(Fault) as error:
        broker.submit("painter_run", ACTION, "old:edit", "old")
    assert error.value.code == "RUNTIME_CHANGED"


def test_concurrent_duplicate_submission_executes_once(broker, adapter):
    errors = []

    def call():
        try:
            submit(broker)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    pump_all(broker)
    assert not errors
    assert adapter.value == 1
    assert len(broker.records) == 1


def test_result_expiry_keeps_tombstone(engine, adapter):
    now = [0.0]
    broker = Broker(engine, clock=lambda: now[0], result_ttl=10, capacity=2)
    receipt = submit(broker)
    pump_all(broker)
    now[0] = 11
    assert broker.status(receipt["request_id"])["result_expired"]
    assert submit(broker)["result_expired"]
    pump_all(broker)
    assert adapter.value == 1
    submit(broker, suffix="two")
    with pytest.raises(Fault) as error:
        submit(broker, suffix="three")
    assert error.value.code == "RECOVERY_CAPACITY"
    broker.close()


def test_result_memory_eviction_does_not_repeat_edits(engine, adapter):
    broker = Broker(engine, result_bytes=1)
    receipt = submit(broker)
    pump_all(broker)
    assert broker.status(receipt["request_id"])["result_expired"]
    submit(broker)
    assert adapter.value == 1


def test_busy_queue_stays_queued_and_cancelled_work_never_executes(broker, adapter):
    adapter.is_busy = True
    receipt = submit(broker)
    broker.pump()
    assert broker.status(receipt["request_id"])["state"] == "queued"
    result = broker.cancel(receipt["request_id"])
    assert result["state"] == "failed"
    assert result["result"]["structuredContent"]["data"]["side_effects_possible"] is False
    adapter.is_busy = False
    pump_all(broker)
    assert adapter.value == 0


def test_running_work_cannot_be_cancelled(broker):
    receipt = submit(broker)
    broker.pump()
    with pytest.raises(Fault) as error:
        broker.cancel(receipt["request_id"])
    assert error.value.code == "CANCELLATION_UNSUPPORTED"


def test_app_job_controls_bypass_busy_work(broker, adapter):
    original = submit(broker)
    broker.pump()
    adapter.is_busy = True
    control = submit(
        broker,
        {"steps": [{"id": "job", "op": "job.status", "args": {"job_id": "test"}}]},
        "control",
    )
    broker.pump()
    assert broker.status(control["request_id"])["state"] == "completed"
    assert broker.status(original["request_id"])["state"] == "running"
    adapter.is_busy = False


def test_shutdown_fails_queued_and_partial_active_work(broker, adapter):
    running = submit(broker)
    queued = submit(broker, suffix="queued")
    broker.pump()
    broker.close()
    assert broker.status(running["request_id"])["result"]["structuredContent"]["data"][
        "side_effects_possible"
    ]
    assert not broker.status(queued["request_id"])["result"]["structuredContent"]["data"][
        "side_effects_possible"
    ]
    assert adapter.value == 1
    with pytest.raises(Fault, match="shutting down"):
        submit(broker, suffix="late")


def test_queue_limit(engine):
    broker = Broker(engine, queue_limit=1)
    submit(broker)
    with pytest.raises(Fault) as error:
        submit(broker, suffix="second")
    assert error.value.code == "QUEUE_FULL"
    broker.close()
