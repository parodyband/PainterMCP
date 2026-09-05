"""Private loopback JSON transport. MCP itself is provided by the external stdio process."""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .common import MAX_INPUT, Fault, dumps


class LimitedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Transport:
    def __init__(self, broker, port=0, token=None):
        self.broker = broker
        self.token = token or secrets.token_urlsafe(32)
        transport = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self):
                super().setup()
                self.connection.settimeout(7)

            def log_message(self, *args):
                pass

            def reply(self, status, value):
                payload = dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def authenticated(self):
                expected_host = f"127.0.0.1:{transport.port}"
                if (
                    self.headers.get("Origin") is not None
                    or self.headers.get("Host") != expected_host
                ):
                    self.reply(403, {"error": {"code": "ORIGIN_REJECTED"}})
                    return False
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, "Bearer " + transport.token):
                    self.reply(401, {"error": {"code": "AUTH_REQUIRED"}})
                    return False
                return True

            def do_GET(self):
                if not self.authenticated():
                    return
                if self.path != "/health":
                    self.reply(404, {"error": {"code": "NOT_FOUND"}})
                    return
                self.reply(200, transport.broker.health())

            def do_POST(self):
                if not self.authenticated():
                    return
                try:
                    if self.headers.get("Transfer-Encoding"):
                        raise Fault("INVALID_HTTP", "Chunked requests are not accepted")
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > MAX_INPUT:
                        raise Fault("INPUT_LIMIT", "JSON request must be between 1 byte and 1 MiB")
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise Fault("INVALID_HTTP", "Incomplete body")
                    data = json.loads(
                        body, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x))
                    )
                    if not isinstance(data, dict):
                        raise Fault("INVALID_ARGUMENT", "Expected a JSON object")
                    if self.path == "/submit":
                        if not isinstance(data.get("tool"), str) or not isinstance(
                            data.get("args", {}), dict
                        ):
                            raise Fault("INVALID_ARGUMENT", "Expected tool string and args object")
                        args = data.get("args", {})
                        request_id = args.get("request_id", data.get("request_id"))
                        if request_id is not None and (
                            not isinstance(request_id, str) or len(request_id) > 4096
                        ):
                            raise Fault(
                                "INVALID_ARGUMENT",
                                "request_id must be a string of at most 4096 characters",
                            )
                        wait_ms = args.get("wait_ms", data.get("wait_ms", 1000))
                        if not isinstance(wait_ms, int) or not 0 <= wait_ms <= 5000:
                            raise Fault("INVALID_ARGUMENT", "wait_ms must be 0..5000")
                        receipt = transport.broker.submit(
                            data["tool"], args, request_id, data.get("runtime_id")
                        )
                        if wait_ms:
                            receipt = transport.broker.wait(receipt["request_id"], wait_ms / 1000)
                        self.reply(200, receipt)
                    elif self.path == "/request":
                        request_id = data.get("request_id")
                        if not isinstance(request_id, str):
                            raise Fault("INVALID_ARGUMENT", "request_id must be a string")
                        action = data.get("action", "status")
                        if action not in ("status", "cancel"):
                            raise Fault("INVALID_ARGUMENT", "Expected status or cancel")
                        result = (
                            transport.broker.cancel(request_id)
                            if action == "cancel"
                            else transport.broker.status(request_id)
                        )
                        self.reply(200, result)
                    else:
                        self.reply(404, {"error": {"code": "NOT_FOUND"}})
                except Fault as exc:
                    self.reply(400, {"error": exc.json()})
                except (TimeoutError, ValueError, TypeError, RecursionError) as exc:
                    self.reply(400, {"error": {"code": "INVALID_JSON", "message": str(exc)[:200]}})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = LimitedHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="PainterMCP-HTTP",
            daemon=True,
        )

    def start(self):
        self.thread.start()

    def close(self):
        self.broker.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
