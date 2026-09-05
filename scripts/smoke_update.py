"""Exercise a built release archive with a real isolated venv and restart/rollback selection."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from painter_mcp import __version__, bootstrap, updater
from painter_mcp.install import install


def smoke():
    dist = Path("dist").resolve()
    manifest = json.loads((dist / "release-manifest.json").read_text())
    payload = (dist / manifest["package"]["name"]).read_bytes()
    with tempfile.TemporaryDirectory(prefix="painter-mcp-upgrade-") as temp:
        root = Path(temp).resolve()
        os.environ["PAINTER_MCP_HOME"] = str(root)
        os.environ.pop("PAINTER_MCP_CONNECTION", None)
        install(root / "painter-python", clients=["codex", "claude"], user_home=root / "user")
        update = {
            "state": "available",
            "version": __version__,
            "sha256": manifest["package"]["sha256"],
            "compatibility": manifest["compatibility"],
            "asset": {
                "size": len(payload),
                "state": "uploaded",
                "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/v{__version__}/{manifest['package']['name']}",
            },
        }
        staged = updater.stage_update(update, root, fetch=lambda *args: payload)
        assert staged["state"] == "staged"
        assert not (root / "active-version.json").exists()
        active = bootstrap.activate_pending(root, "12.1.4")
        assert active["version"] == __version__
        # This is the identical stable launcher registered in both client configs.
        command = [sys.executable, str(root / "launch.py"), "--version"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        assert result.stdout.strip() == __version__, result.stdout
        assert updater.rollback(root)["restart_required"]
        previous = bootstrap.activate_pending(root, "12.1.4")
        assert previous["baseline"]
        assert (
            subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
            == __version__
        )
        print(
            "Verified built archive -> real versioned venv -> pending activation -> stable launcher -> rollback."
        )


if __name__ == "__main__":
    smoke()
