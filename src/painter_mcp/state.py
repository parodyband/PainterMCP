"""Bounded, explicitly scoped state. No dependency on Adobe modules."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from typing import Any

from .common import Fault, digest, dumps

COVERAGE = {
    "model": "snapshot comparison of returned project/stack/channel/layer/selection facts",
    "not_covered": [
        "pixel values and brush strokes",
        "unreturned layers/effects beyond limit",
        "resource file contents",
        "shader evaluation",
        "viewport render completion",
        "intermediate changes that return to the same facts",
        "other texture sets",
        "source parameters and projection/geometry-mask values not returned in the facts",
    ],
    "atomic": False,
}


class State:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self.observations: OrderedDict[str, dict] = OrderedDict()
        self.pages: OrderedDict[str, dict] = OrderedDict()
        self.sessions: OrderedDict[str, dict] = OrderedDict()
        self.handles: OrderedDict[str, Any] = OrderedDict()
        self.on_reset = None

    def reset_project(self):
        self.epoch = uuid.uuid4().hex
        self.observations.clear()
        self.pages.clear()
        self.sessions.clear()
        self.handles.clear()
        if self.on_reset:
            self.on_reset()

    def ref(self, kind, identity):
        return f"{kind}:{self.epoch}:{identity}"

    def identity(self, reference: str, kind: str):
        try:
            actual, epoch, identity = reference.split(":", 2)
        except ValueError as exc:
            raise Fault("INVALID_REFERENCE", "Expected a returned Painter reference") from exc
        if epoch != self.epoch:
            raise Fault("STALE_REFERENCE", "Project was replaced; observe again")
        if actual != kind:
            raise Fault("INVALID_REFERENCE", f"Expected {kind}, received {actual}")
        return identity

    def retain(self, value):
        for token, existing in self.handles.items():
            if existing is value:
                return token
        if len(self.handles) >= 512:
            self.handles.popitem(last=False)
        token = self.ref("object", uuid.uuid4().hex)
        self.handles[token] = value
        return token

    def dereference(self, token):
        self.identity(token, "object")
        if token not in self.handles:
            raise Fault("HANDLE_EXPIRED", "Object handle evicted; query the owning node/API again")
        self.handles.move_to_end(token)
        return self.handles[token]

    def observe(self, scope, facts):
        token = self.ref("observation", uuid.uuid4().hex)
        if len(self.observations) >= 64:
            self.observations.popitem(last=False)
        self.observations[token] = {
            "scope": scope,
            "fingerprint": digest(facts),
            "created": self.clock(),
        }
        return token

    def observation(self, token):
        self.identity(token, "observation")
        item = self.observations.get(token)
        if not item or self.clock() - item["created"] > 600:
            self.observations.pop(token, None)
            raise Fault("OBSERVATION_EXPIRED", "Observation expired; observe again")
        return item

    def check(self, token, current_facts):
        item = self.observation(token)
        if item["fingerprint"] != digest(current_facts):
            raise Fault(
                "STALE_OBSERVATION",
                "Covered facts changed; observe and reconsider the edit",
                coverage=COVERAGE,
            )

    def paginate(self, key, producer, args):
        limit = args.get("limit", 50)
        offset = args.get("offset", 0)
        cursor = args.get("cursor")
        if cursor:
            try:
                token, offset_text = cursor.rsplit("/", 1)
                offset = int(offset_text)
                entry = self.pages[token]
            except (KeyError, ValueError) as exc:
                raise Fault("PAGE_EXPIRED", "Page snapshot unavailable; repeat the query") from exc
            if entry["key"] != key or self.clock() - entry["created"] > 120:
                raise Fault("PAGE_EXPIRED", "Page belongs to another query or has expired")
            items = entry["items"]
        else:
            items = producer()
            if len(dumps(items).encode()) > 4_000_000:
                raise Fault("QUERY_TOO_LARGE", "Narrow the resource query or texture-set scope")
            while self.pages and (
                len(self.pages) >= 16
                or sum(x["size"] for x in self.pages.values()) + len(dumps(items)) > 8_000_000
            ):
                self.pages.popitem(last=False)
            token = self.ref("page", uuid.uuid4().hex)
            self.pages[token] = {
                "key": key,
                "items": items,
                "created": self.clock(),
                "size": len(dumps(items)),
            }
        if offset < 0 or offset > len(items):
            raise Fault("INVALID_CURSOR", "Page offset out of bounds")
        # Limit bytes as well as item count; preserve a continuation for every omitted item.
        page = []
        used = 0
        for item in items[offset : offset + limit]:
            size = len(dumps(item).encode())
            if size > 48_000:
                raise Fault(
                    "ITEM_TOO_LARGE",
                    "An individual result exceeds the page budget; use a narrower API query",
                )
            if used + size > 48_000:
                break
            page.append(item)
            used += size
        end = offset + len(page)
        return {
            "items": page,
            "total": len(items),
            "offset": offset,
            "next_cursor": f"{token}/{end}" if end < len(items) else None,
            "truncated": end < len(items),
            "snapshot": True,
            "expires_in_seconds": 120,
        }

    def session(self, action, session_id=None):
        for token, item in list(self.sessions.items()):
            if self.clock() - item["used"] > 1800:
                del self.sessions[token]
        if action == "open":
            if len(self.sessions) >= 16:
                raise Fault("SESSION_LIMIT", "Close unused sessions; at most 16 may be retained")
            session_id = self.ref("session", uuid.uuid4().hex)
            self.sessions[session_id] = {"namespace": {}, "kept": {}, "used": self.clock()}
        elif session_id not in self.sessions:
            raise Fault(
                "SESSION_EXPIRED",
                "Session closed, idle-expired or project replaced; open a new session",
            )
        if action == "close":
            del self.sessions[session_id]
            return {"session_id": session_id, "closed": True, "cancelled_work": False}
        item = self.sessions[session_id]
        item["used"] = self.clock()
        if action == "reset":
            item["namespace"].clear()
            item["kept"].clear()
        return {
            "session_id": session_id,
            "idle_ttl_seconds": 1800,
            "kept_results": len(item["kept"]),
            "sdk": [
                "call(op, args)",
                "observe(**options)",
                "node(ref)",
                "keep(json)",
                "get(token)",
                "release(token)",
            ],
            "namespace_memory_bounded": False,
            "full_host_privileges": True,
        }
