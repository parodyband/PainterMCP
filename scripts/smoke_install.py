"""Install the built wheel in a fresh venv, set up isolated clients, repair and uninstall."""

import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path

from painter_mcp import __version__


def smoke():
    wheel = next(Path("dist").resolve().glob(f"painter_mcp-{__version__}-*.whl"))
    with tempfile.TemporaryDirectory(prefix="painter-mcp-install-test-") as temp:
        root = Path(temp)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {**os.environ, "PAINTER_MCP_HOME": str(root / "runtime")}
        env.pop("PAINTER_MCP_CONNECTION", None)

        def run(*args, success=True):
            result = subprocess.run([str(python), *args], env=env, capture_output=True, text=True)
            if success and result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result

        run("-m", "pip", "install", str(wheel))
        installed = run(
            "-m",
            "painter_mcp",
            "install",
            "--painter-python",
            str(root / "painter-python"),
            "--user-home",
            str(root / "user"),
            "--clients",
            "codex",
            "claude",
        )
        assert json.loads(installed.stdout)["installed"]
        assert run("-m", "painter_mcp", "--version").stdout.strip() == __version__
        assert run("-m", "painter_mcp", "doctor", success=False).returncode == 1
        plugin = root / "painter-python/modules/painter_mcp/plugin.py"
        plugin.unlink()
        assert json.loads(run("-m", "painter_mcp", "repair").stdout)["installed"]
        assert plugin.exists()
        run("-m", "painter_mcp", "uninstall")
        assert not plugin.exists()
        assert not (root / "painter-python/startup/painter_mcp_startup.py").exists()
        print(
            "Wheel installed; both clients configured; missing plugin repaired; uninstall preserved unrelated files."
        )


if __name__ == "__main__":
    smoke()
