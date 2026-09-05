"""Thread-safe queue and bounded recovery; never calls Painter from HTTP threads."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict, deque

from . import __version__
from .common import Fault, digest, dumps, envelope


class Broker:
    def __init__(
        self,
        engine,
        clock=time.monotonic,
        capacity=4096,
        result_bytes=32_000_000,
        result_ttl=900,
        queue_limit=64,
    ):
        self.engine = engine
        self.clock = clock
        self.runtime_id = uuid.uuid4().hex
        self.capacity = capacity
        self.result_bytes = result_bytes
        self.result_ttl = result_ttl
        self.queue_limit = queue_limit
        self.records: OrderedDict[str, dict] = OrderedDict()
        self.queue: deque[str] = deque()
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.active = None
        self.iterator = None
        self.executing = False
        self.closed = False
        self.capabilities = engine.adapter.capabilities()
        self.last_pump = clock()

    def health(self):
        with self.lock:
            return {
                "version": __version__,
                "runtime_id": self.runtime_id,
                "closed": self.closed,
                "queued": len(self.queue),
                "active_request": self.active,
                "main_thread_last_seen_seconds": round(self.clock() - self.last_pump, 3),
                "capabilities": self.capabilities,
                "recovery": {
                    "id_prefix": self.runtime_id + ":",
                    "max_ids": self.capacity,
                    "retained_ids": len(self.records),
                    "result_ttl_seconds": self.result_ttl,
                    "result_byte_budget": self.result_bytes,
                    "durable": False,
                    "eviction": "results expire, ID tombstones remain until runtime ends",
                },
                "limits": {
                    "queue": self.queue_limit,
                    "steps": 64,
                    "facts_bytes": 65536,
                    "encoded_image_bytes": 2000000,
                    "request_bytes": 1048576,
                },
            }

    def submit(self, tool, args, request_id=None, runtime_id=None):
        self.engine.preflight(tool, args)
        clean = {k: v for k, v in args.items() if k not in ("request_id", "wait_ms")}
        fingerprint = digest({"tool": tool, "args": clean})
        with self.condition:
            if self.closed:
                raise Fault("SHUTDOWN", "Painter MCP is shutting down")
            if runtime_id != self.runtime_id:
                raise Fault(
                    "RUNTIME_CHANGED",
                    "Runtime restarted. Inspect project state before submitting a new edit",
                )
            request_id = request_id or self.runtime_id + ":" + uuid.uuid4().hex
            if not request_id.startswith(self.runtime_id + ":"):
                raise Fault(
                    "RUNTIME_CHANGED",
                    "Request IDs must begin with the current runtime_id followed by :",
                )
            if request_id in self.records:
                record = self.records[request_id]
                if record["fingerprint"] != fingerprint:
                    raise Fault("REQUEST_ID_CONFLICT", "ID already belongs to different arguments")
                return self.status(request_id)
            self.prune()
            if len(self.records) >= self.capacity:
                raise Fault(
                    "RECOVERY_CAPACITY",
                    "ID tombstone capacity reached; finish/recover outstanding requests then restart plugin",
                )
            if len(self.queue) >= self.queue_limit:
                raise Fault("QUEUE_FULL", "Wait for queued work to complete")
            self.records[request_id] = {
                "request_id": request_id,
                "state": "queued",
                "fingerprint": fingerprint,
                "tool": tool,
                "args": clean,
                "created": self.clock(),
                "result": None,
                "result_size": 0,
            }
            self.queue.append(request_id)
            return self.status(request_id)

    def status(self, request_id):
        with self.lock:
            self.prune()
            if request_id not in self.records:
                code = (
                    "RUNTIME_CHANGED"
                    if not request_id.startswith(self.runtime_id + ":")
                    else "REQUEST_UNKNOWN"
                )
                raise Fault(
                    code,
                    "No recoverable request. Inspect state before deciding whether a new edit is needed",
                )
            record = self.records[request_id]
            data = {
                "request_id": request_id,
                "runtime_id": self.runtime_id,
                "state": record["state"],
                "age_seconds": round(self.clock() - record["created"], 3),
            }
            if record["state"] in ("completed", "failed"):
                data.update(
                    result=record["result"],
                    result_expired=record["result"] is None,
                    resubmission_executes=False,
                )
            return data

    def wait(self, request_id, seconds):
        deadline = self.clock() + min(seconds, 5)
        with self.condition:
            while self.records[request_id]["state"] in ("queued", "running") and not self.closed:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.status(request_id)

    def cancel(self, request_id):
        with self.condition:
            self.status(request_id)
            record = self.records[request_id]
            if record["state"] == "queued":
                self.queue.remove(request_id)
                self.finish(
                    request_id,
                    envelope(
                        {
                            "error": {"code": "CANCELLED_BEFORE_START"},
                            "side_effects_possible": False,
                        },
                        ok=False,
                    ),
                )
            elif record["state"] == "running":
                raise Fault(
                    "CANCELLATION_UNSUPPORTED",
                    "Running workflows/scripts cannot be forcibly cancelled; baking has job.cancel",
                )
            return self.status(request_id)

    def finish(self, request_id, result):
        with self.condition:
            record = self.records[request_id]
            record.update(
                state="failed" if result["isError"] else "completed",
                result=result,
                result_size=len(dumps(result).encode()),
                finished=self.clock(),
            )
            record.pop("args", None)
            self.prune()
            self.condition.notify_all()

    def prune(self):
        total = sum(r["result_size"] for r in self.records.values())
        for record in self.records.values():
            if record["result"] is not None and (
                total > self.result_bytes
                or self.clock() - record.get("finished", self.clock()) > self.result_ttl
            ):
                total -= record["result_size"]
                record.update(result=None, result_size=0)

    def pump(self):
        """One generator advance per Qt tick. Controls/status do not wait for this method."""
        self.last_pump = self.clock()
        if self.closed:
            return
        control_key = None
        control_iterator = None
        busy = self.engine.adapter.busy()
        with self.lock:
            if self.active is None:
                if not self.queue:
                    return
                # job.status and job.cancel bypass busy gating, but still run on the app thread.
                index = next(
                    (i for i, key in enumerate(self.queue) if self._control(self.records[key])),
                    None,
                )
                if index is not None:
                    request_id = self.queue[index]
                    del self.queue[index]
                elif busy:
                    return
                else:
                    request_id = self.queue.popleft()
                self.active = request_id
                record = self.records[request_id]
                record["state"] = "running"
                self.iterator = self.engine.execute(record["tool"], record["args"])
            elif busy and not self._control(self.records[self.active]):
                # Permit app-job controls while a yielded workflow waits for a bake/reload.
                control_key = next((k for k in self.queue if self._control(self.records[k])), None)
                if control_key:
                    self.queue.remove(control_key)
                    self.records[control_key]["state"] = "running"
                    control = self.records[control_key]
                    control_iterator = self.engine.execute(control["tool"], control["args"])
                else:
                    return
            request_id = self.active
            iterator = self.iterator
        if control_iterator is not None:
            try:
                while True:
                    next(control_iterator)
            except StopIteration as end:
                self.finish(control_key, end.value)
            except BaseException as exc:
                self.finish(control_key, envelope({"error": self.engine.error(exc)}, ok=False))
            return
        # Do not hold recovery lock while running Painter or user scripts.
        try:
            self.executing = True
            assert iterator is not None
            next(iterator)
        except StopIteration as end:
            self.finish(request_id, end.value)
            self.active = self.iterator = None
        except BaseException as exc:
            self.finish(
                request_id,
                envelope(
                    {
                        "error": self.engine.error(exc),
                        "side_effects_possible": True,
                        "atomic": False,
                        "rolled_back": False,
                    },
                    ok=False,
                ),
            )
            self.active = self.iterator = None
        finally:
            self.executing = False
            if self.closed and iterator is not None:
                iterator.close()

    @staticmethod
    def _control(record):
        return (
            record["tool"] == "painter_run"
            and all(s["op"] in ("job.status", "job.cancel") for s in record["args"]["steps"])
            and not record["args"].get("observe")
        )

    def close(self):
        with self.condition:
            self.closed = True
            for request_id in list(self.queue):
                self.finish(
                    request_id,
                    envelope(
                        {
                            "error": {"code": "SHUTDOWN_BEFORE_START"},
                            "side_effects_possible": False,
                        },
                        ok=False,
                    ),
                )
            self.queue.clear()
            if self.active:
                self.finish(
                    self.active,
                    envelope(
                        {
                            "error": {"code": "SHUTDOWN_DURING_WORKFLOW"},
                            "side_effects_possible": True,
                            "rolled_back": False,
                        },
                        ok=False,
                    ),
                )
            if self.iterator and not self.executing:
                self.iterator.close()
            self.active = self.iterator = None
            self.condition.notify_all()
