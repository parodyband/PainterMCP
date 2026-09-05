import hashlib
import io
import json
import os
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from painter_mcp import bootstrap, updater
from painter_mcp.common import Fault
from painter_mcp.install import managed_files, repair


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def release():
    version = "1.2.0"
    wheel_files = {
        f"painter_mcp/{name}": b"# Test package\n"
        for name in ("plugin.py", "updater.py", "bootstrap.py", "server.py", "data/SKILL.md")
    }
    wheel_files["painter_mcp/__init__.py"] = f'__version__ = "{version}"\n'.encode()
    wheel_files[f"painter_mcp-{version}.dist-info/METADATA"] = (
        f"Metadata-Version: 2.1\nName: painter-mcp\nVersion: {version}\n".encode()
    )
    wheel = zipped(wheel_files)
    files = {f"painter_mcp-{version}-py3-none-any.whl": wheel}
    files["manifest.json"] = json.dumps(
        {
            "version": version,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        }
    ).encode()
    payload = zipped({f"painter-mcp-{version}/{name}": data for name, data in files.items()})

    def asset(name, data):
        return {
            "name": name,
            "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "state": "uploaded",
            "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/v{version}/{name}",
        }

    package = asset(f"painter-mcp-{version}.zip", payload)
    manifest = {
        "schema_version": 1,
        "name": "painter-mcp",
        "version": version,
        "compatibility": updater.COMPATIBILITY,
        "package": {
            "name": package["name"],
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }
    metadata = json.dumps(manifest).encode()
    manifest_asset = asset("release-manifest.json", metadata)
    github = {
        "tag_name": "v" + version,
        "draft": False,
        "prerelease": False,
        "assets": [manifest_asset, package],
    }
    responses = {
        updater.LATEST: json.dumps(github).encode(),
        manifest_asset["browser_download_url"]: metadata,
        package["browser_download_url"]: payload,
    }

    def fetch(url, maximum, expected=None, stop=None):
        updater.cancelled(stop)
        return responses[url]

    return {
        "github": github,
        "manifest": manifest,
        "payload": payload,
        "fetch": fetch,
        "responses": responses,
        "update": updater.query_update("12.1.4", "1.1.0", fetch=fetch),
    }


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)
    root = tmp_path / "install"
    root.mkdir()
    monkeypatch.setenv("PAINTER_MCP_HOME", str(root))
    painter = tmp_path / "painter"
    original = painter / "modules/painter_mcp/__init__.py"
    managed_files({original: '__version__ = "1.1.0"\n'}, painter / "painter-mcp-install.json")
    (root / "installation.json").write_text(
        json.dumps(
            {
                "python": sys.executable,
                "painter_python": str(painter),
                "user_home": str(tmp_path / "user"),
                "clients": ["codex"],
            }
        ),
        encoding="utf-8",
    )

    def runner(command, stop):
        updater.cancelled(stop)
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.touch()
        return "1.2.0\n" if command[-1] == "--version" else ""

    return root, original, runner


def test_available_no_release_up_to_date_and_incompatible(release):
    assert release["update"]["state"] == "available"
    assert updater.query_update("12.1.4", "1.2.0", release["fetch"])["state"] == "up_to_date"
    assert updater.query_update("13.0.0", "1.1.0", release["fetch"])["state"] == "incompatible"
    assert updater.query_update("12.1.4", fetch=lambda *args: None)["state"] == "no_release"


@pytest.mark.parametrize(
    "field,value", [("tag_name", "v1.3.0"), ("prerelease", True), ("draft", True)]
)
def test_release_identity_mismatch(release, field, value):
    release["github"][field] = value
    release["responses"][updater.LATEST] = json.dumps(release["github"]).encode()
    with pytest.raises(Fault):
        updater.query_update("12.1.4", "1.1.0", release["fetch"])


def test_manifest_asset_digest_rejected(release):
    release["github"]["assets"][0]["digest"] = "sha256:" + "0" * 64
    release["responses"][updater.LATEST] = json.dumps(release["github"]).encode()
    with pytest.raises(Fault, match="digest"):
        updater.query_update("12.1.4", "1.1.0", release["fetch"])


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "a\\b",
        "a/../x",
        "CON.py",
        "a./b",
        "file:stream",
        "a /b",
    ],
)
def test_unsafe_archive_paths_rejected(name):
    payload = zipped({name: b"unsafe"})
    if name == "a\\b":
        payload = payload.replace(b"a/b", b"a\\b")
    with pytest.raises(Fault):
        updater.archive_files(payload)


def test_symlink_duplicate_names_and_expanded_limits_rejected(monkeypatch):
    for names in (("A.py", "a.py"), ("same", "same")):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, b"x")
        with pytest.raises(Fault):
            updater.archive_files(buffer.getvalue())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"/etc/passwd")
    with pytest.raises(Fault):
        updater.archive_files(buffer.getvalue())
    monkeypatch.setattr(updater, "MAX_DOWNLOAD", 10)
    with pytest.raises(Fault):
        updater.archive_files(zipped({"big": b"x" * 11}))


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/parodyband/PainterMCP/releases/download/v1/a",
        "https://evil.example/a",
        "https://github.com/other/repo/releases/download/v1/a",
        "https://user:password@github.com/parodyband/PainterMCP/releases/download/v1/a",
    ],
)
def test_download_origin_restrictions(url):
    with pytest.raises(Fault):
        updater.trusted_url(url, asset=True)


def test_tampered_zip_never_runs_installer(release, installed):
    root, original, runner = installed
    with pytest.raises(Fault):
        updater.stage_update(
            release["update"],
            root,
            fetch=lambda *args: b"bad",
            runner=lambda *args: pytest.fail("must not run"),
        )
    assert original.exists()
    assert not (root / "pending-update.json").exists()


def test_stage_restart_switch_and_rollback(release, installed):
    root, original, runner = installed
    before = original.read_bytes()
    result = updater.stage_update(release["update"], root, release["fetch"], runner)
    assert result["state"] == "staged"
    assert original.read_bytes() == before
    assert not (root / "active-version.json").exists()
    assert updater.stage_update(release["update"], root, release["fetch"], runner)["already_staged"]
    active = bootstrap.activate_pending(root, "12.1.4")
    assert active["version"] == "1.2.0"
    assert not (root / "pending-update.json").exists()
    assert updater.rollback(root)["state"] == "staged"
    assert bootstrap.activate_pending(root, "12.1.4")["baseline"]


def test_modified_files_block_staging_and_late_edits_block_activation(release, installed):
    root, original, runner = installed
    before = original.read_bytes()
    original.write_text("# User edit")
    with pytest.raises(Fault, match="edited"):
        updater.stage_update(release["update"], root, release["fetch"], runner)
    original.write_bytes(before)
    updater.stage_update(release["update"], root, release["fetch"], runner)
    original.write_text("# Late edit")
    assert bootstrap.activate_pending(root, "12.1.4") is None
    assert not (root / "active-version.json").exists()
    assert (root / "activation-error.json").exists()


def test_interruption_preserves_current_install(release, installed):
    root, original, runner = installed
    stop = threading.Event()

    def interrupted(command, cancel):
        stop.set()
        updater.cancelled(stop)

    with pytest.raises(Fault):
        updater.stage_update(release["update"], root, release["fetch"], interrupted, stop)
    assert not (root / "pending-update.json").exists()
    assert not list((root / "versions").iterdir())
    assert original.exists()


def test_user_skill_preserved_without_enrolling_new_client(release, installed):
    root, original, runner = installed
    user = Path(bootstrap.read(root / "installation.json")["user_home"])
    skill = user / ".agents/skills/painter-mcp/SKILL.md"
    managed_files({skill: "old skill"}, skill.parent / ".managed.json")
    skill.write_text("user instructions")
    staged = updater.stage_update(release["update"], root, release["fetch"], runner)
    assert not staged["skills"]["codex"]["installed"]
    assert skill.read_text() == "user instructions"
    assert (skill.parent / "SKILL.md.incoming-1.2.0").exists()
    assert not (user / ".claude").exists()


def test_repair_versioned_package_preserves_edits(release, installed):
    root, original, runner = installed
    updater.stage_update(release["update"], root, release["fetch"], runner)
    record = bootstrap.activate_pending(root, "12.1.4")
    plugin = Path(record["directory"]) / "modules/painter_mcp/plugin.py"
    plugin.unlink()
    assert repair()["installed"] and plugin.exists()
    plugin.write_text("# My customization")
    assert not repair()["installed"]
    assert plugin.read_text() == "# My customization"


def test_check_throttle_and_shutdown_drops_late_result(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "query_update", lambda *args, **kwargs: {"state": "no_release"})
    worker = updater.UpdateWorker("12.1.4", tmp_path)
    assert worker.check()
    worker.thread.join(2)
    assert worker.result["state"] == "no_release"
    assert not worker.check()
    assert worker.check(manual=True)
    worker.close()
    assert not worker.check(manual=True)
    monkeypatch.setenv("PAINTER_MCP_DISABLE_UPDATE_CHECK", "1")
    assert not updater.UpdateWorker("12.1.4", tmp_path / "new").check()


def test_activation_does_not_race_preparation(release, installed):
    root, original, runner = installed
    updater.stage_update(release["update"], root, release["fetch"], runner)
    with updater.install_lock(root):
        assert bootstrap.activate_pending(root, "12.1.4") is None
    assert bootstrap.activate_pending(root, "12.1.4")["version"] == "1.2.0"
