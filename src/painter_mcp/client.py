"""External client; no application imports, silent stdout, no automatic edit retries."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .common import MAX_INPUT, MAX_RESULT, Fault, connection_path, dumps, envelope


class Client:
    def __init__(self, path: Path | None = None):
        self.path = path or connection_path()
        self.connection = None
        # Do not send local bearer credentials through a user-configured HTTP proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Fault(
                "NOT_CONNECTED",
                "Start Painter with the Painter MCP plugin, then run painter-mcp doctor",
                connection_file=str(self.path),
            ) from exc
        if (
            data.get("host") != "127.0.0.1"
            or not isinstance(data.get("port"), int)
            or not 1 <= data["port"] <= 65535
        ):
            raise Fault(
                "INVALID_CONNECTION",
                "Connection file must specify a loopback address and valid port",
            )
        if not isinstance(data.get("token"), str) or not data.get("runtime_id"):
            raise Fault(
                "INVALID_CONNECTION",
                "Connection file lacks authentication/runtime data; restart plugin",
            )
        self.connection = data
        return data

    def request(self, path, body=None, reload=False):
        connection = self.load() if reload or self.connection is None else self.connection
        payload = dumps(body).encode() if body is not None else None
        if payload and len(payload) > MAX_INPUT:
            raise Fault("INPUT_LIMIT", "Request exceeds 1 MiB")
        request = urllib.request.Request(
            f"http://127.0.0.1:{connection['port']}{path}",
            data=payload,
            headers={
                "Authorization": "Bearer " + connection["token"],
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(MAX_RESULT * 2 + 1)
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read(8192)).get("error", {})
            except (ValueError, OSError):
                error = {}
            raise Fault(
                error.get("code", "HTTP_ERROR"),
                error.get("message", f"HTTP {exc.code}; run painter-mcp doctor"),
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise Fault(
                "CONNECTION_LOST",
                "Painter connection failed; outcome may be unknown. Recover the request ID before repeating edits",
            ) from exc
        if len(raw) > MAX_RESULT * 2:
            raise Fault(
                "RESPONSE_LIMIT",
                "Bridge response exceeds transport budget; execution may already have completed",
            )
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise Fault("INVALID_RESPONSE", "Bridge returned invalid JSON") from exc

    def health(self):
        return self.request("/health", reload=True)

    def submit(self, tool, args, wait_ms=1000):
        connection = self.load()
        request_id = args.get("request_id", connection["runtime_id"] + ":" + uuid.uuid4().hex)
        try:
            return self.request(
                "/submit",
                {
                    "tool": tool,
                    "args": args,
                    "wait_ms": wait_ms,
                    "request_id": request_id,
                    "runtime_id": connection["runtime_id"],
                },
            )
        except Fault as exc:
            exc.details["request_id"] = request_id
            raise

    def status(self, request_id, action="status"):
        return self.request("/request", {"request_id": request_id, "action": action}, reload=True)

    def call_tool(self, name, args):
        try:
            if name == "painter_status":
                return envelope(self.health())
            receipt = (
                self.status(args["request_id"], args.get("action", "status"))
                if name == "painter_request"
                else self.submit(name, args)
            )
            return render_receipt(receipt)
        except Fault as exc:
            return envelope({"error": exc.json(), "request_id": args.get("request_id")}, ok=False)


def render_receipt(receipt):
    """Lift recovered images into native MCP content without base64 in structured facts."""
    execution = receipt.get("result")
    request = {k: v for k, v in receipt.items() if k != "result"}
    if execution is None:
        return envelope(
            {
                "request": request,
                "execution_available": False,
                "hint": "Poll painter_request using request_id; queued/running is not completion",
            }
        )
    return envelope(
        {**execution["structuredContent"]["data"], "request": request},
        execution["content"][1:],
        ok=not execution["isError"],
    )
