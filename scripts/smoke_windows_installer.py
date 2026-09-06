"""Run the actual double-click launcher paths on Windows, isolated from user configuration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from painter_mcp import __version__


def smoke(remote=False):
    if os.name != "nt":
        print("Windows launcher validation requires Windows.")
        return
    archive = (Path("dist") / f"painter-mcp-{__version__}.zip").resolve()
    with tempfile.TemporaryDirectory(prefix="painter-mcp-cmd-test-") as temporary:
        root = Path(temporary).resolve()
        assert root.parent == Path(tempfile.gettempdir()).resolve()
        extracted = root / "extracted & spaced"
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
        directory = extracted / f"painter-mcp-{__version__}"
        env = {
            **os.environ,
            "PAINTER_MCP_HOME": str(root / "state"),
            "PAINTER_MCP_INSTALLER_ROOT": str(root / "state/venv"),
            "PAINTER_MCP_INSTALLER_PAINTER_PYTHON": str(root / "painter-python"),
            "PAINTER_MCP_INSTALLER_USER_HOME": str(root / "user"),
            "PAINTER_MCP_INSTALLER_NO_PAUSE": "1",
            "PAINTER_MCP_INSTALLER_FORCE_PRIVATE_PYTHON": "1",
            "PAINTER_MCP_INSTALLER_CLIENTS": "codex,claude",
        }
        for name in (
            "PAINTER_MCP_CONNECTION",
            "PAINTER_MCP_INSTALLER_ARCHIVE",
            "PAINTER_MCP_INSTALLER_SHA256",
            "PAINTER_MCP_NO_DELEGATE",
        ):
            env.pop(name, None)

        def launch(folder, expected=0, extra=None):
            result = subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "Install-PainterMcp.cmd"],
                cwd=folder,
                env={**env, **(extra or {})},
                capture_output=True,
                text=True,
                errors="replace",
                timeout=360,
            )
            if result.returncode != expected:
                raise AssertionError(result.stdout + result.stderr)
            return result.stdout

        # Corruption must be rejected before executing setup or installing prerequisites.
        installer = directory / "install.ps1"
        original = installer.read_bytes()
        installer.write_bytes(original + b"\nthrow 'corrupt'\n")
        rejected = launch(directory, expected=1)
        assert "SHA-256 verification failed" in rejected, rejected
        assert not (root / "state/venv").exists()
        installer.write_bytes(original)

        # This exercises the no-existing-Python branch, including a real official private runtime.
        launch(directory)
        python = root / "state/venv/Scripts/python.exe"
        assert python.exists()
        assert (root / "state/runtimes/python-3.13.15/tools/python.exe").exists()
        config = (root / "state/venv/pyvenv.cfg").read_text()
        assert "runtimes" in config
        assert (root / "painter-python/startup/painter_mcp_startup.py").exists()
        clients = json.loads((root / "user/.claude.json").read_text())
        assert clients["mcpServers"]["painter"]["args"][-1] == "serve"
        assert (root / "user/.agents/skills/painter-mcp/SKILL.md").exists()

        # Windows Explorer can extract only the clicked CMD. Keep no sibling setup files.
        standalone = root / "only launcher & spaces"
        standalone.mkdir()
        shutil.copy2(directory / "Install-PainterMcp.cmd", standalone)
        fixture = {
            "PAINTER_MCP_INSTALLER_ARCHIVE": str(archive),
            "PAINTER_MCP_INSTALLER_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        launch(standalone, extra={} if remote else fixture)
        assert "SHA-256 verification failed" in launch(
            standalone, expected=1, extra={**fixture, "PAINTER_MCP_INSTALLER_SHA256": "0" * 64}
        )
        unsafe = root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as package:
            package.writestr("../escape.txt", "must not escape")
        assert "Unsafe archive path" in launch(
            standalone,
            expected=1,
            extra={
                "PAINTER_MCP_INSTALLER_ARCHIVE": str(unsafe),
                "PAINTER_MCP_INSTALLER_SHA256": hashlib.sha256(unsafe.read_bytes()).hexdigest(),
            },
        )
        assert not (root / "escape.txt").exists()
        version = subprocess.check_output(
            [str(python), "-m", "painter_mcp", "--version"], env=env, text=True
        )
        assert version.strip() == __version__
        print(
            "Passed: extracted CMD, private Python provisioning, standalone CMD bootstrap, both clients, corrupted package/hash and unsafe archive rejection."
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--remote", action="store_true", help="Validate the published GitHub download path"
    )
    smoke(parser.parse_args().remote)
