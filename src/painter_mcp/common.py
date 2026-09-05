"""Wire contracts shared by the embedded plugin and external client (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

MAX_INPUT = 1_048_576
MAX_FACTS = 65_536
MAX_IMAGE = 2_000_000
MAX_RESULT = 3_000_000


class Fault(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details

    def json(self):
        return {"code": self.code, "message": str(self)[:2000], **self.details}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def home() -> Path:
    return Path(os.environ.get("PAINTER_MCP_HOME", str(Path.home() / ".painter-mcp")))


def connection_path() -> Path:
    return Path(os.environ.get("PAINTER_MCP_CONNECTION", str(home() / "connection.json")))


def atomic_write(path: Path, content: str, private: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            if private:
                os.chmod(temp, 0o600)
                if os.name == "nt":
                    import csv
                    import subprocess

                    identity = subprocess.run(
                        ["whoami", "/user", "/fo", "csv", "/nh"],
                        capture_output=True,
                        text=True,
                        check=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    sid = next(csv.reader([identity.stdout.strip()]))[1]
                    subprocess.run(
                        ["icacls", str(temp), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                        capture_output=True,
                        check=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            stream.write(content)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def pointer(value: Any, path: str):
    if path == "":
        return value
    if not path.startswith("/"):
        raise Fault("INVALID_REFERENCE", "JSON pointer must be empty or start with /")
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        raise Fault("INVALID_REFERENCE", f"Cannot resolve pointer {path}") from exc


def resolve(value: Any, results: dict):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            step, sep, path = value["$ref"].partition("#")
            if not sep or step not in results:
                raise Fault("INVALID_REFERENCE", "Reference must target an earlier successful step")
            return pointer(results[step], path)
        return {k: resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    return value


def validate(value: Any, schema: dict, path: str = "$", refs: bool = False):
    """Validate the deliberately small JSON Schema subset used in our catalog."""
    if refs and isinstance(value, dict) and set(value) == {"$ref"}:
        if not isinstance(value["$ref"], str):
            raise Fault("INVALID_ARGUMENT", f"{path}: $ref must be a string")
        return
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option, path, refs)
                return
            except Fault:
                pass
        raise Fault("INVALID_ARGUMENT", f"{path}: no matching schema")
    for condition in schema.get("allOf", []):
        validate(value, condition, path, refs)
    condition_unknown = (
        refs
        and isinstance(value, dict)
        and any(
            isinstance(value.get(key), dict) and set(value[key]) == {"$ref"}
            for key in schema.get("if", {}).get("properties", {})
        )
    )
    if "if" in schema and not condition_unknown:
        try:
            validate(value, schema["if"], path, refs)
        except Fault:
            if "else" in schema:
                validate(value, schema["else"], path, refs)
        else:
            validate(value, schema.get("then", {}), path, refs)
    if "const" in schema and value != schema["const"]:
        raise Fault("INVALID_ARGUMENT", f"{path}: expected {schema['const']!r}")
    kind = schema.get("type")
    kinds: dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "null": type(None),
    }
    if kind and (
        not isinstance(value, kinds[kind])
        or (kind in ("number", "integer") and isinstance(value, bool))
    ):
        raise Fault("INVALID_ARGUMENT", f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise Fault("INVALID_ARGUMENT", f"{path}: expected one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        missing = set(schema.get("required", [])) - value.keys()
        unknown = (
            value.keys() - props.keys() if schema.get("additionalProperties") is False else set()
        )
        if missing or unknown:
            raise Fault(
                "INVALID_ARGUMENT", f"{path}: missing {sorted(missing)}, unknown {sorted(unknown)}"
            )
        for key, item in value.items():
            validate(item, props.get(key, {}), f"{path}.{key}", refs)
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", 10000) or len(value) < schema.get("minItems", 0):
            raise Fault("INVALID_ARGUMENT", f"{path}: invalid array length")
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{i}]", refs)
    if isinstance(value, str) and len(value) > schema.get("maxLength", MAX_INPUT):
        raise Fault("INVALID_ARGUMENT", f"{path}: string too long")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -float("inf")) or value > schema.get(
            "maximum", float("inf")
        ):
            raise Fault("INVALID_ARGUMENT", f"{path}: number out of range")


def envelope(data: dict, images: list | None = None, ok: bool = True):
    facts = {"ok": ok, "data": data}
    if len(dumps(facts).encode()) > MAX_FACTS:
        # Never convert a successful edit into an execution error due to presentation limits.
        summary: dict = {
            "truncated": True,
            "reason": "fact_byte_limit",
            "execution_succeeded": ok,
            "hint": "Use select, pagination, or SDK keep().",
        }
        for key in ("undo", "request", "partial_failure", "side_effects_possible", "elapsed_ms"):
            if key in data:
                summary[key] = data[key]
        if "error" in data:
            summary["error"] = {
                "code": str(data["error"].get("code", "ERROR"))[:80],
                "message": str(data["error"].get("message", ""))[:512],
                "details_truncated": True,
            }
        if "steps" in data:
            summary["steps"] = []
            for step in data["steps"]:
                item = {
                    k: v
                    for k, v in step.items()
                    if k
                    in ("id", "state", "undo", "may_have_modified_project", "side_effects_possible")
                }
                for key in ("error", "presentation_error"):
                    if key in step:
                        item[key] = {
                            "code": str(step[key].get("code", "ERROR"))[:80],
                            "message": str(step[key].get("message", ""))[:512],
                            "details_truncated": True,
                        }
                item["output_truncated"] = "result" in step
                summary["steps"].append(item)
        if "observation" in data:
            summary["observation"] = {
                "ok": data["observation"].get("ok"),
                "truncated": True,
                "refresh_required": True,
            }
        facts = {"ok": ok, "data": summary}
    return {
        "isError": not ok,
        "structuredContent": facts,
        "content": [{"type": "text", "text": dumps(facts)}, *(images or [])],
    }
