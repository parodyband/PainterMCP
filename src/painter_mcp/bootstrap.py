"""Stable, stdlib-only startup/stdio launcher copied outside versioned packages.

The registry changes only at Painter startup, never during a running project.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

_plugin = None


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def changed(files):
    return [
        name
        for name, digest in files.items()
        if not Path(name).is_file() or Path(name).is_symlink() or fingerprint(Path(name)) != digest
    ]


def validate_record(root, record, check_files=True):
    directory = Path(record["directory"])
    versions = (root / "versions").resolve()
    if directory.is_symlink() or directory.resolve().parent != versions:
        raise RuntimeError("Update directory is outside the managed versions directory")
    if not (directory / "ready.json").is_file():
        raise RuntimeError("Update preparation did not complete")
    if check_files and changed(record["files"]):
        raise RuntimeError("Staged update was changed or damaged; restage it")
    executable = Path(record["python"])
    if record.get("baseline") and executable == Path(read(root / "installation.json")["python"]):
        if not executable.is_file():
            raise RuntimeError("Base installation interpreter is missing")
        return
    if not executable.is_file() or not executable.resolve().is_relative_to(directory.resolve()):
        # POSIX venv Python can legitimately be a symlink to the base interpreter.
        if executable.parent.parent != directory / "venv" or not executable.is_file():
            raise RuntimeError("Invalid versioned Python interpreter")


@contextlib.contextmanager
def registry_lock(root):
    with (root / "update.lock").open("a+b") as stream:
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        if os.name == "nt":
            module = importlib.import_module("msvcrt")
            module.locking(stream.fileno(), module.LK_NBLCK, 1)
        else:
            module = importlib.import_module("fcntl")
            module.flock(stream, module.LOCK_EX | module.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                module.locking(stream.fileno(), module.LK_UNLCK, 1)
            else:
                module.flock(stream, module.LOCK_UN)


def activate_pending(root, painter_version, api=None):
    try:
        with registry_lock(root):
            return _activate_pending(root, painter_version, api)
    except OSError:
        return None  # A preparation is in progress. Keep the current package for this launch.


def _activate_pending(root, painter_version, api=None):
    pending = root / "pending-update.json"
    if not pending.exists():
        return None
    record = read(pending)
    try:
        validate_record(root, record)
        if changed(record.get("watched", {})):
            raise RuntimeError("Managed files changed since staging; run repair before activating")
        version = tuple(int(x) for x in painter_version.split(".")[:3])
        compatibility = record["compatibility"]
        if tuple(sys.version_info[:2]) < tuple(compatibility["python_min"]):
            raise RuntimeError("Staged update requires a newer Painter Python interpreter")
        if api is not None:
            for name in compatibility.get("required_api", []):
                value = api
                for part in name.split("."):
                    if not hasattr(value, part):
                        raise RuntimeError(f"Staged update needs unavailable Painter API: {name}")
                    value = getattr(value, part)
        if (
            not tuple(compatibility["painter_min"])
            <= version
            < tuple(compatibility["painter_max_exclusive"])
        ):
            raise RuntimeError("Staged update is incompatible with this Painter version")
        active = root / "active-version.json"
        previous = read(active) if active.exists() else record.get("previous")
        if previous and previous["directory"] != record["directory"]:
            write(root / "previous-version.json", previous)
        write(active, record)
        pending.unlink()
        (root / "activation-error.json").unlink(missing_ok=True)
        return record
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        write(root / "activation-error.json", {"error": str(exc), "version": record.get("version")})
        return None


def start_painter():
    global _plugin
    root = Path(__file__).parent
    import substance_painter as sp

    activate_pending(root, sp.application.version(), sp)
    active = root / "active-version.json"
    if active.exists():
        record = read(active)
        validate_record(root, record, check_files=False)
        sys.path.insert(0, str(Path(record["directory"]) / "modules"))
    _plugin = importlib.import_module("painter_mcp.plugin")
    _plugin.start_plugin()


def stop_painter():
    global _plugin
    if _plugin is not None:
        _plugin.close_plugin()
        _plugin = None


def launch_client(root=None, arguments=None):
    root = Path(root or Path(__file__).parent)
    config = read(root / "installation.json")
    active = root / "active-version.json"
    executable = config["python"]
    if active.exists():
        record = read(active)
        validate_record(root, record, check_files=False)
        executable = record["python"]
    env = {**os.environ, "PAINTER_MCP_NO_DELEGATE": "1"}
    return subprocess.call(
        [executable, "-m", "painter_mcp", *(arguments if arguments is not None else sys.argv[1:])],
        env=env,
    )
