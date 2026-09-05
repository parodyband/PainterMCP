"""Agent loop and SDK, independent of Painter's threading and socket transport."""

from __future__ import annotations

import contextlib
import io
import time
import uuid

from .catalog import CORE, OBSERVE, OPS, operation
from .common import Fault, digest, dumps, envelope, pointer, resolve, validate
from .state import COVERAGE


class BoundedText(io.TextIOBase):
    def __init__(self, limit=8192):
        self.text = ""
        self.limit = limit
        self.truncated = False

    def write(self, value):
        remaining = self.limit - len(self.text)
        self.text += value[:remaining]
        self.truncated |= len(value) > remaining
        return len(value)


class NodeHandle:
    def __init__(self, adapter, reference):
        self.adapter = adapter
        self.reference = reference

    @property
    def ref(self):
        self.adapter.object(self.reference)
        return self.reference

    @property
    def name(self):
        return self.adapter.object(self.reference).get_name()


class SDK:
    def __init__(self, engine, session):
        self.engine = engine
        self.session = session
        self.calls = 0
        self.active = True
        self.images = []

    def check(self):
        if not self.active:
            raise Fault("STALE_CELL", "Use this cell's painter SDK; do not retain SDK objects")
        self.calls += 1
        if self.calls > 128:
            raise Fault("SDK_CALL_LIMIT", "Split the script into smaller cells")

    def call(self, op, args=None):
        self.check()
        return self.engine.call(op, args or {})

    def observe(self, **options):
        self.check()
        if len(self.images) >= 1:
            raise Fault(
                "IMAGE_LIMIT",
                "One SDK observation image per cell; use post-observation for verification",
            )
        data, images = self.engine.observe(options)
        self.images.extend(images)
        return data

    def node(self, reference):
        self.check()
        self.engine.adapter.object(reference)
        return NodeHandle(self.engine.adapter, reference)

    def keep(self, value):
        self.check()
        encoded = dumps(value)
        kept = self.session["kept"]
        if len(kept) >= 64 or sum(len(dumps(v)) for v in kept.values()) + len(encoded) > 4_000_000:
            raise Fault(
                "KEEP_LIMIT", "Release retained results before keeping more (64 results / 4 MB)"
            )
        token = "result:" + uuid.uuid4().hex
        # Copy so callers cannot accidentally mutate retained JSON.
        kept[token] = __import__("json").loads(encoded)
        return token

    def get(self, token):
        self.check()
        if token not in self.session["kept"]:
            raise Fault("RESULT_EXPIRED", "Retained result unavailable")
        return __import__("json").loads(dumps(self.session["kept"][token]))

    def release(self, token):
        self.check()
        return self.session["kept"].pop(token, None) is not None


class Engine:
    def __init__(self, adapter, state):
        self.adapter = adapter
        self.state = state

    def preflight(self, tool, args):
        if tool not in CORE:
            raise Fault("UNKNOWN_TOOL", f"Unknown tool {tool}")
        validate(args, CORE[tool][1])
        if tool == "painter_run":
            earlier: set[str] = set()
            for step in args["steps"]:
                if not step["id"] or step["id"] in earlier or "#" in step["id"]:
                    raise Fault(
                        "INVALID_STEP_ID", "Step IDs must be nonempty, unique and contain no #"
                    )
                spec = operation(step["op"])
                validate(step.get("args", {}), spec["inputSchema"], refs=True)
                self.check_references(step.get("args", {}), earlier)
                if (
                    args.get("undo") == "layerstack"
                    and spec["mutates"]
                    and spec["undo"] != "layerstack"
                ):
                    raise Fault(
                        "UNDO_SCOPE_UNSUPPORTED",
                        "Grouped undo permits only layerstack mutations and read operations",
                    )
                earlier.add(step["id"])
            if "observe" in args:
                validate(args["observe"], OBSERVE, refs=True)
                self.check_references(args["observe"], earlier)

    def check_references(self, value, earlier):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                target, sep, path = value["$ref"].partition("#")
                if not sep or target not in earlier or (path and not path.startswith("/")):
                    raise Fault(
                        "INVALID_REFERENCE", "References must use earlierStep#/json/pointer"
                    )
            else:
                for child in value.values():
                    self.check_references(child, earlier)
        elif isinstance(value, list):
            for child in value:
                self.check_references(child, earlier)

    def call(self, name, args):
        spec = operation(name)
        validate(args, spec["inputSchema"])
        return self.adapter.invoke(name, args)

    def observe(self, options):
        validate(options, OBSERVE)
        facts = self.adapter.facts(options)
        scope = {
            k: v for k, v in options.items() if k in ("texture_set", "stack", "limit", "nodes")
        }
        # Pin implicit active scope to a returned texture-set ID to avoid changing what the guard watches.
        if "texture_set" in facts:
            scope.update(texture_set=facts["texture_set"]["ref"], stack=facts["stack"]["name"])
        token = self.state.observe(scope, facts)
        data = {
            **facts,
            "observation_id": token,
            "cursor": token,
            "scope": scope,
            "coverage": COVERAGE,
        }
        images = []
        try:
            images, data["image"] = self.adapter.capture(options)
        except Exception as exc:
            data["image"] = {"error": self.error(exc), "captured": False}
        return data, images

    def guard(self, token):
        old = self.state.observation(token)
        self.state.check(token, self.adapter.facts(old["scope"]))

    @staticmethod
    def error(exc):
        return (
            exc.json()
            if isinstance(exc, Fault)
            else {
                "code": "APPLICATION_ERROR",
                "message": str(exc)[:2000],
                "type": type(exc).__name__,
            }
        )

    def execute(self, tool, args):
        """Generator advances on the application thread; each ungrouped step yields to Qt."""
        self.preflight(tool, args)
        if args.get("if_observation"):
            self.guard(args["if_observation"])
        if tool == "painter_observe":
            data, images = self.observe(args)
            return envelope(data, images)
        if tool == "painter_changes":
            try:
                old = self.state.observation(args["cursor"])
                current = self.adapter.facts(old["scope"])
                changed = old["fingerprint"] != digest(current)
                return envelope(
                    {
                        "changed": changed,
                        "requires_observation": changed,
                        "cursor": args["cursor"],
                        "coverage": COVERAGE,
                    }
                )
            except Fault as exc:
                return envelope(
                    {"requires_observation": True, "reason": exc.json(), "coverage": COVERAGE}
                )
        if tool == "painter_session":
            return envelope(self.state.session(args["action"], args.get("session_id")))
        if tool == "painter_describe":
            names = args.get("names")
            domain = args.get("domain")
            if not names and not domain:
                return envelope(
                    {
                        "domains": sorted({n.split(".")[0] for n in OPS}),
                        "operation_count": len(OPS),
                        "hint": "Request names or one domain",
                    }
                )
            selected = names or [n for n in OPS if n.startswith(domain + ".")]
            capabilities = self.adapter.capabilities()["operations"]
            return envelope(
                self.state.paginate(
                    "describe:" + dumps(selected),
                    lambda: [{**operation(n), **capabilities[n]} for n in selected],
                    args,
                )
            )
        if tool == "painter_script":
            result, images, ok = self.script(args)
            yield
            if "observe" in args:
                self.post_observe(result, images, args["observe"])
            return envelope(result, images, ok)
        if tool != "painter_run":
            raise Fault("INVALID_DISPATCH", "Transport control sent to application execution")
        started = time.perf_counter()
        full: dict = {}
        records = []
        images = []
        failed = False
        grouped = args.get("undo") == "layerstack"
        undo = {
            "requested": args.get("undo", "individual"),
            "grouped": grouped,
            "coverage": "layerstack mutations only" if grouped else "per-operation native behavior",
            "rolled_back": False,
            "atomic": False,
        }
        manager = (
            self.adapter.undo_scope("Painter MCP batch") if grouped else contextlib.nullcontext()
        )
        with manager:
            for step in args["steps"]:
                if failed:
                    records.append({"id": step["id"], "state": "skipped"})
                    continue
                spec = operation(step["op"])
                try:
                    value = self.call(step["op"], resolve(step.get("args", {}), full))
                    # Earlier references see unprojected results. Retention is bounded per batch.
                    full[step["id"]] = value
                    record = {
                        "id": step["id"],
                        "state": "completed",
                        "undo": spec["undo"],
                        "may_have_modified_project": spec["mutates"],
                    }
                    records.append(record)
                    if len(dumps(full).encode()) > 4_000_000:
                        record.update(
                            output_truncated=True, presentation_error={"code": "BATCH_RESULT_LIMIT"}
                        )
                        failed = True
                    else:
                        if "select" in step:
                            value = {path: pointer(value, path) for path in step["select"]}
                        record["result"] = value
                except Exception as exc:
                    failed = True
                    # A projection error must not erase evidence that the operation already completed.
                    if (
                        records
                        and records[-1]["id"] == step["id"]
                        and records[-1]["state"] == "completed"
                    ):
                        records[-1]["presentation_error"] = self.error(exc)
                    else:
                        records.append(
                            {
                                "id": step["id"],
                                "state": "failed",
                                "error": self.error(exc),
                                "side_effects_possible": spec["mutates"],
                                "undo": spec["undo"],
                            }
                        )
                if not grouped:
                    yield
        data = {
            "steps": records,
            "undo": undo,
            "partial_failure": failed,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if grouped:
            yield
        if "observe" in args:
            try:
                options = resolve(args["observe"], full)
                self.post_observe(data, images, options)
            except Exception as exc:
                data["observation"] = {
                    "ok": False,
                    "error": self.error(exc),
                    "edits_repeated": False,
                }
        return envelope(data, images, not failed)

    def post_observe(self, data, images, options):
        try:
            observation, captured = self.observe(options)
            if captured:
                observation["image"]["image_index"] = 1 + len(images)
            data["observation"] = {"ok": True, "data": observation}
            data["observation_image_indices"] = list(
                range(1 + len(images), 1 + len(images) + len(captured))
            )
            images.extend(captured)
        except Exception as exc:
            data["observation"] = {"ok": False, "error": self.error(exc), "edits_repeated": False}

    def script(self, args):
        if args.get("session_id"):
            self.state.session("status", args["session_id"])
            session = self.state.sessions[args["session_id"]]
        else:
            session = {"namespace": {}, "kept": {}}
        sdk = SDK(self, session)
        namespace = session["namespace"]
        namespace.pop("result", None)
        namespace.update(arguments=args.get("arguments", {}), painter=sdk)
        output = BoundedText()
        data = {
            "undo": {"coverage": "script-dependent", "rolled_back": False, "atomic": False},
            "full_host_privileges": True,
            "side_effects_possible": True,
        }
        ok = True
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                if args.get("language", "python") == "javascript":
                    # Function-local arguments/result avoid leaking state into unrelated JS plugins.
                    source = (
                        "(function(arguments){var result;\n"
                        + args["source"]
                        + "\n;return JSON.stringify(result===undefined?null:result);})("
                        + dumps(args.get("arguments", {}))
                        + ")"
                    )
                    value = self.adapter.sp.js.evaluate(source)
                    data["result"] = __import__("json").loads(value) if value else None
                else:
                    exec(compile(args["source"], "<painter-mcp>", "exec"), namespace, namespace)
                    data["result"] = self.adapter.encode(namespace.get("result"))
        except BaseException as exc:
            ok = False
            data["error"] = self.error(exc)
        finally:
            sdk.active = False
        data.update(stdout=output.text, stdout_truncated=output.truncated, sdk_calls=sdk.calls)
        return data, sdk.images, ok
