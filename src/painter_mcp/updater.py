"""Verified GitHub Releases downloads and side-by-side preparation; no Adobe imports."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import __version__, bootstrap
from .common import Fault, atomic_write, dumps, home

REPOSITORY = "parodyband/PainterMCP"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_FILES = 2048
INTERVAL = 86400
COMPATIBILITY = {
    "painter_min": [12, 1, 0],
    "painter_max_exclusive": [13, 0, 0],
    "python_min": [3, 10],
    "platforms": ["windows-x64", "macos-arm64", "macos-x64", "linux-x64"],
}
COMPATIBILITY["required_api"] = [
    "layerstack.ScopedModification",
    "layerstack.get_node_by_uid",
    "project.is_busy",
    "event.DISPATCHER",
]


def version_key(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise Fault("INVALID_UPDATE", "Release version must be major.minor.patch")
    return tuple(int(x) for x in version.split("."))


def platform_key():
    system = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), "unknown"
    )
    arch = {"AMD64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine(), "unknown"
    )
    return f"{system}-{arch}"


def cancelled(stop):
    if stop is not None and stop.is_set():
        raise Fault("UPDATE_CANCELLED", "Update stopped before activation")


def trusted_url(url, asset=False):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise Fault("INVALID_UPDATE", "Update URLs must use trusted HTTPS endpoints")
    prefix = f"/{REPOSITORY}/releases/download/" if asset else f"/repos/{REPOSITORY}/releases/"
    host = "github.com" if asset else "api.github.com"
    if (
        parsed.hostname != host
        or not parsed.path.startswith(prefix)
        or parsed.query
        or parsed.fragment
    ):
        raise Fault("INVALID_UPDATE", "Update URL does not belong to the PainterMCP repository")


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
            "github.com",
            "api.github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        }:
            raise Fault("INVALID_UPDATE", "Untrusted release download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, maximum, expected_size=None, stop=None):
    trusted_url(url, asset=not url.startswith("https://api.github.com/"))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"painter-mcp/{__version__}",
            "Accept": "application/octet-stream"
            if "download/" in url
            else "application/vnd.github+json",
        },
    )
    deadline = time.monotonic() + 60
    try:
        with urllib.request.build_opener(Redirects()).open(request, timeout=5) as response:
            if int(response.headers.get("Content-Length", 0)) > maximum:
                raise Fault("UPDATE_SIZE_LIMIT", "Release download exceeds its size limit")
            buffer = bytearray()
            while True:
                cancelled(stop)
                if time.monotonic() > deadline:
                    raise Fault("UPDATE_TIMEOUT", "Release download exceeded 60 seconds")
                chunk = response.read(min(65536, maximum + 1 - len(buffer)))
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > maximum:
                    raise Fault("UPDATE_SIZE_LIMIT", "Release download exceeds its size limit")
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and url == LATEST:
            return None
        raise Fault("UPDATE_NETWORK", f"GitHub returned HTTP {exc.code}; retry later") from exc
    except (OSError, ValueError) as exc:
        raise Fault("UPDATE_NETWORK", str(exc)) from exc
    if expected_size is not None and len(buffer) != expected_size:
        raise Fault("UPDATE_INTEGRITY", "Downloaded size does not match release metadata")
    return bytes(buffer)


def asset_payload(asset, maximum, fetch=download, stop=None):
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= maximum or asset.get("state") != "uploaded":
        raise Fault("INVALID_UPDATE", "Invalid release asset size/state")
    trusted_url(asset["browser_download_url"], asset=True)
    payload = fetch(asset["browser_download_url"], maximum, size, stop)
    if payload is None or len(payload) != size:
        raise Fault("UPDATE_INTEGRITY", "Release asset length mismatch")
    digest = asset.get("digest")
    if digest is not None and digest != "sha256:" + hashlib.sha256(payload).hexdigest():
        raise Fault("UPDATE_INTEGRITY", "GitHub asset digest does not match download")
    return payload


def query_update(painter_version, current_version=__version__, fetch=download, stop=None):
    payload = fetch(LATEST, 2_000_000, None, stop)
    if payload is None:
        return {"state": "no_release", "current_version": current_version}
    release = json.loads(payload)
    tag = release.get("tag_name", "")
    version = tag.removeprefix("v")
    if tag != "v" + version or release.get("draft") or release.get("prerelease"):
        raise Fault("INVALID_UPDATE", "Expected a published stable version tag")
    if version_key(version) <= version_key(current_version):
        return {
            "state": "up_to_date",
            "current_version": current_version,
            "latest_version": version,
        }
    assets = {}
    for asset in release.get("assets", []):
        if asset["name"] in assets:
            raise Fault("INVALID_UPDATE", "Duplicate release asset name")
        assets[asset["name"]] = asset
    if "release-manifest.json" not in assets:
        raise Fault("INVALID_UPDATE", "Release has no updater manifest")
    manifest = json.loads(asset_payload(assets["release-manifest.json"], 1_000_000, fetch, stop))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("name") != "painter-mcp"
        or manifest.get("version") != version
    ):
        raise Fault("INVALID_UPDATE", "Release tag and manifest identity disagree")
    compatibility = manifest["compatibility"]
    if (
        not tuple(compatibility["painter_min"])
        <= version_key(painter_version)
        < tuple(compatibility["painter_max_exclusive"])
        or platform_key() not in compatibility["platforms"]
    ):
        return {
            "state": "incompatible",
            "latest_version": version,
            "painter_version": painter_version,
        }
    package = manifest["package"]
    expected_name = f"painter-mcp-{version}.zip"
    if package.get("name") != expected_name or expected_name not in assets:
        raise Fault("INVALID_UPDATE", "Release is missing its matching installer archive")
    asset = assets[expected_name]
    if package.get("size") != asset.get("size") or not re.fullmatch(
        r"[a-f0-9]{64}", package.get("sha256", "")
    ):
        raise Fault("INVALID_UPDATE", "Manifest and GitHub package metadata disagree")
    if asset.get("digest") not in (None, "sha256:" + package["sha256"]):
        raise Fault("UPDATE_INTEGRITY", "Manifest and GitHub package hashes disagree")
    trusted_url(asset["browser_download_url"], asset=True)
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{expected_name}"
    if asset["browser_download_url"] != expected_url:
        raise Fault("INVALID_UPDATE", "Archive URL does not match its version tag")
    return {
        "state": "available",
        "version": version,
        "asset": asset,
        "sha256": package["sha256"],
        "compatibility": compatibility,
        "release_url": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
    }


def archive_files(payload):
    """Reject traversal, Windows aliases, duplicate/case-colliding names and ZIP bombs."""
    files = {}
    seen = set()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if not 0 < len(members) <= MAX_FILES or sum(m.file_size for m in members) > MAX_DOWNLOAD:
            raise Fault("UPDATE_SIZE_LIMIT", "Expanded update archive exceeds limits")
        for member in members:
            name = member.orig_filename
            parts = name.rstrip("/").split("/")
            if (
                name.startswith("/")
                or any(ord(c) < 32 for c in name)
                or "\\" in name
                or any(
                    p in ("", ".", "..")
                    or ":" in p
                    or p.endswith((".", " "))
                    or p.split(".")[0].upper()
                    in {
                        "CON",
                        "PRN",
                        "AUX",
                        "NUL",
                        *[f"COM{i}" for i in range(10)],
                        *[f"LPT{i}" for i in range(10)],
                    }
                    for p in parts
                )
                or member.flag_bits & 1
                or (member.external_attr >> 16) & 0o170000 == 0o120000
                or name.casefold() in seen
            ):
                raise Fault("UNSAFE_ARCHIVE", f"Unsafe update member: {name}")
            seen.add(name.casefold())
            if not member.is_dir():
                files[name] = archive.read(member)
    return files


def verify_archive(payload, update):
    if (
        len(payload) != update["asset"]["size"]
        or hashlib.sha256(payload).hexdigest() != update["sha256"]
    ):
        raise Fault("UPDATE_INTEGRITY", "Installer archive hash/size mismatch")
    files = archive_files(payload)
    prefix = f"painter-mcp-{update['version']}/"
    manifest_name = prefix + "manifest.json"
    manifest = json.loads(files[manifest_name])
    if manifest["version"] != update["version"] or set(files) != {
        prefix + n for n in manifest["files"]
    } | {manifest_name}:
        raise Fault("INVALID_UPDATE", "Installer contents do not match its manifest")
    for name, digest in manifest["files"].items():
        if hashlib.sha256(files[prefix + name]).hexdigest() != digest:
            raise Fault("UPDATE_INTEGRITY", "Installer member hash mismatch")
    wheel_name = f"painter_mcp-{update['version']}-py3-none-any.whl"
    wheel = files[prefix + wheel_name]
    contents = archive_files(wheel)
    init = contents["painter_mcp/__init__.py"].decode()
    if not re.search(r"__version__\s*=\s*[\'\"]" + re.escape(update["version"]) + r"[\'\"]", init):
        raise Fault("INVALID_UPDATE", "Wheel package version disagrees with release")
    metadata = contents[f"painter_mcp-{update['version']}.dist-info/METADATA"].decode()
    if (
        f"\nVersion: {update['version']}\n" not in metadata
        or "\nName: painter-mcp\n" not in metadata
    ):
        raise Fault("INVALID_UPDATE", "Wheel metadata identity mismatch")
    for required in ("plugin.py", "updater.py", "bootstrap.py", "server.py", "data/SKILL.md"):
        if "painter_mcp/" + required not in contents:
            raise Fault("INVALID_UPDATE", f"Wheel is missing {required}")
    return wheel_name, wheel, contents


@contextlib.contextmanager
def install_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = bootstrap.registry_lock(root)
    try:
        lock.__enter__()
    except OSError as exc:
        raise Fault("UPDATE_BUSY", "Another installer is already preparing an update") from exc
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def run_process(command, stop=None, timeout=240):
    env = {
        k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    }
    env["PAINTER_MCP_NO_DELEGATE"] = "1"
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            stdout=output,
            stderr=output,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                cancelled(stop)
                if time.monotonic() > deadline or output.tell() > 4_000_000:
                    raise Fault("UPDATE_TIMEOUT", "Update preparation exceeded time/output limits")
                time.sleep(0.1)
            output.seek(0)
            message = output.read(4_000_000).decode(errors="replace")
            if process.returncode:
                raise Fault("UPDATE_PREPARATION", message[-4000:])
            return message
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        timeout=5,
                    )
                else:
                    kill_group = getattr(os, "killpg", None)
                    assert kill_group is not None
                    kill_group(process.pid, getattr(signal, "SIGKILL", 9))
                process.wait(timeout=10)


def watched_files(root):
    config = bootstrap.read(root / "installation.json")
    active = root / "active-version.json"
    if active.exists():
        record = bootstrap.read(active)
        bootstrap.validate_record(root, record)
        files = dict(record["files"])
    else:
        manifest = bootstrap.read(Path(config["painter_python"]) / "painter-mcp-install.json")
        files = dict(manifest["files"])
    conflicts = bootstrap.changed(files)
    if conflicts:
        raise Fault(
            "UPDATE_CONFLICT",
            "Managed plugin files were edited or removed; repair/merge before updating",
            files=conflicts,
        )
    return files


def baseline_record(root, compatibility):
    config = bootstrap.read(root / "installation.json")
    directory = Path(tempfile.mkdtemp(prefix="baseline-", dir=root / "versions"))
    package = Path(config["painter_python"]) / "modules/painter_mcp"
    files = {}
    for original in package.glob("*.py"):
        target = directory / "modules/painter_mcp" / original.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        files[str(target)] = bootstrap.fingerprint(target)
    record = {
        "version": __version__,
        "directory": str(directory),
        "python": config["python"],
        "files": files,
        "compatibility": compatibility,
        "baseline": True,
    }
    atomic_write(directory / "ready.json", dumps(record))
    return record


def stage_update(update, root=None, fetch=download, runner=run_process, stop=None):
    root = Path(root or home()).resolve()
    with install_lock(root):
        if update.get("state") != "available":
            raise Fault("INVALID_UPDATE", "Only an available verified release can be staged")
        watched = watched_files(root)
        pending_path = root / "pending-update.json"
        if pending_path.exists():
            pending = bootstrap.read(pending_path)
            if version_key(pending["version"]) >= version_key(update["version"]):
                bootstrap.validate_record(root, pending)
                return {
                    "state": "staged",
                    "version": pending["version"],
                    "restart_required": True,
                    "already_staged": True,
                }
        payload = asset_payload(update["asset"], MAX_DOWNLOAD, fetch, stop)
        wheel_name, wheel, contents = verify_archive(payload, update)
        versions = root / "versions"
        versions.mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=update["version"] + "-", dir=versions)).resolve()
        try:
            wheel_path = directory / wheel_name
            wheel_path.write_bytes(wheel)
            files = {str(wheel_path): hashlib.sha256(wheel).hexdigest()}
            for name, data in contents.items():
                if name.startswith("painter_mcp/"):
                    path = directory / "modules" / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    files[str(path)] = hashlib.sha256(data).hexdigest()
            config = bootstrap.read(root / "installation.json")
            uv = shutil.which("uv")
            command = (
                [uv, "venv", "--seed", "--python", config["python"], str(directory / "venv")]
                if uv
                else [config["python"], "-m", "venv", str(directory / "venv")]
            )
            runner(command, stop)
            python = (
                directory / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            runner(
                [
                    str(python),
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--only-binary=:all:",
                    str(wheel_path),
                ],
                stop,
            )
            actual = runner([str(python), "-m", "painter_mcp", "--version"], stop).strip()
            if actual != update["version"]:
                raise Fault(
                    "UPDATE_INTEGRITY", "Prepared interpreter loaded the wrong package version"
                )
            # Prepare companion skill proposals, but enroll no new clients and preserve modified skills.
            skill_status = {}
            from .install import managed_files

            for client in config["clients"]:
                skill_root = Path(config["user_home"]) / (
                    ".agents/skills/painter-mcp"
                    if client == "codex"
                    else ".claude/skills/painter-mcp"
                )
                if (skill_root / ".managed.json").exists():
                    skill_status[client] = managed_files(
                        {skill_root / "SKILL.md": contents["painter_mcp/data/SKILL.md"].decode()},
                        skill_root / ".managed.json",
                        version=update["version"],
                    )
            cancelled(stop)
            if bootstrap.changed(watched):
                raise Fault(
                    "UPDATE_CONFLICT",
                    "Managed files changed while downloading; update was not activated",
                )
            record = {
                "version": update["version"],
                "directory": str(directory),
                "python": str(python),
                "files": files,
                "watched": watched,
                "compatibility": update["compatibility"],
                "skills": skill_status,
            }
            if not (root / "active-version.json").exists():
                record["previous"] = baseline_record(root, update["compatibility"])
            atomic_write(directory / "ready.json", dumps(record))
            atomic_write(root / "pending-update.json", dumps(record), private=True)
            return {
                "state": "staged",
                "version": update["version"],
                "restart_required": True,
                "skills": skill_status,
            }
        except BaseException:
            # Only this newly created child of our versions root can be recursively removed.
            if directory.resolve().parent == versions.resolve() and not directory.is_symlink():
                shutil.rmtree(directory, ignore_errors=True)
            raise


def status(root=None):
    root = Path(root or home())
    result: dict = {"installed_version": __version__, "repository": REPOSITORY}
    for label, name in (
        ("active", "active-version.json"),
        ("pending", "pending-update.json"),
        ("last_check", "update-check.json"),
        ("activation_error", "activation-error.json"),
    ):
        path = root / name
        if path.exists():
            record = bootstrap.read(path)
            result[label] = {
                k: v
                for k, v in record.items()
                if k in ("version", "state", "error", "checked_at", "latest_version")
            }
    return result


def rollback(root=None):
    root = Path(root or home())
    with install_lock(root):
        previous = root / "previous-version.json"
        if not previous.exists():
            raise Fault("NO_PREVIOUS_VERSION", "No prior versioned installation is available")
        record = bootstrap.read(previous)
        bootstrap.validate_record(root, record)
        record["watched"] = watched_files(root)
        atomic_write(root / "pending-update.json", dumps(record), private=True)
    return {"state": "staged", "version": record["version"], "restart_required": True}


class UpdateWorker:
    """Owned worker; Qt polls messages instead of worker threads touching application APIs."""

    def __init__(self, painter_version, root=None):
        self.painter_version = painter_version
        self.root = Path(root or home())
        self.stop = threading.Event()
        self.thread = None
        self.result = None
        self.manual = False
        self.busy = False

    def check(self, manual=False):
        if self.busy or self.stop.is_set():
            return False
        if not manual:
            if os.environ.get("PAINTER_MCP_DISABLE_UPDATE_CHECK", "").lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                return False
            state = self.root / "update-check.json"
            if state.exists():
                try:
                    if time.time() - bootstrap.read(state)["checked_at"] < INTERVAL:
                        return False
                except (ValueError, KeyError):
                    pass
        self.manual = manual

        def work():
            atomic_write(
                self.root / "update-check.json",
                dumps({"state": "checking", "checked_at": time.time()}),
            )
            try:
                result = query_update(self.painter_version, stop=self.stop)
            except Exception as exc:
                result = {"state": "error", "error": str(exc)}
            atomic_write(
                self.root / "update-check.json", dumps({**result, "checked_at": time.time()})
            )
            return result

        return self.start(work)

    def start(self, function):
        if self.busy or self.stop.is_set():
            return False
        self.busy = True
        self.result = None

        def work():
            try:
                result = function()
            except Exception as exc:
                result = {"state": "error", "error": str(exc)}
            if not self.stop.is_set():
                self.result = result
            self.busy = False

        self.thread = threading.Thread(target=work, name="PainterMCP-Update", daemon=True)
        self.thread.start()
        return True

    def install(self, update):
        self.manual = True
        return self.start(lambda: stage_update(update, self.root, stop=self.stop))

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=6)
