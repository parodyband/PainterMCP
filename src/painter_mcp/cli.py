"""Install, diagnose or serve Painter MCP."""

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

from . import __version__
from .client import Client
from .common import Fault, connection_path, home


def main():
    active = home() / "active-version.json"
    if (
        active.exists()
        and not os.environ.get("PAINTER_MCP_NO_DELEGATE")
        and (len(sys.argv) < 2 or sys.argv[1] not in ("repair", "install", "uninstall"))
    ):
        from . import bootstrap

        record = bootstrap.read(active)
        if Path(record["python"]).resolve() != Path(sys.executable).resolve():
            raise SystemExit(bootstrap.launch_client(home()))
    parser = argparse.ArgumentParser(prog="painter-mcp")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run the MCP stdio server")
    setup = commands.add_parser(
        "install", help="Install managed Painter plugin and optional client configurations"
    )
    setup.add_argument("--painter-python", type=Path)
    setup.add_argument("--clients", nargs="*", choices=["codex", "claude"], default=[])
    setup.add_argument(
        "--user-home", type=Path, help="Override home for isolated installation tests"
    )
    setup.add_argument(
        "--replace-edited",
        action="store_true",
        help="Back up and replace managed files edited by the user",
    )
    for action in ("repair",):
        command = commands.add_parser(
            action, help="Synchronize managed files with this installed package version"
        )
        command.add_argument("--replace-edited", action="store_true")
    updates = commands.add_parser(
        "update", help="Check GitHub and stage the latest compatible stable release"
    )
    updates.add_argument(
        "--check", action="store_true", help="Check only; do not download/install the package"
    )
    commands.add_parser("update-status", help="Show active/pending update and last-check status")
    commands.add_parser("rollback", help="Stage the previous version for the next Painter start")
    commands.add_parser(
        "uninstall", help="Remove unchanged managed plugin, skill and client entries"
    )
    doctor = commands.add_parser(
        "doctor", help="Connection diagnostics; never prints bearer credentials"
    )
    doctor.add_argument("--connection", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "serve":
            from .server import main as serve

            serve()
            return
        if arguments.command == "install":
            from .install import install

            result = install(
                arguments.painter_python,
                arguments.clients,
                arguments.user_home,
                arguments.replace_edited,
            )
        elif arguments.command == "repair":
            from .install import repair

            result = repair(arguments.replace_edited)
        elif arguments.command in ("update", "update-status", "rollback"):
            from . import updater

            if arguments.command == "update-status":
                result = updater.status()
            elif arguments.command == "rollback":
                result = updater.rollback()
            else:
                health = Client().health()
                result = updater.query_update(health["capabilities"]["painter_version"])
                if result["state"] == "available" and not arguments.check:
                    result = updater.stage_update(result)
        elif arguments.command == "uninstall":
            from .install import uninstall

            result = uninstall()
        else:
            client = Client(arguments.connection)
            health = client.health()
            result = {
                "connected": True,
                "connection_file": str(arguments.connection or connection_path()),
                "version": __version__,
                **health,
            }
            if health["version"] != __version__:
                result["warning"] = (
                    "Client/plugin version mismatch. Restart Painter and the MCP client; use repair if installation files are missing."
                )
        print(json.dumps(result, indent=2))
        conflicts = result.get("installed") is False or any(
            not x.get("configured", True) for x in result.get("clients", {}).values()
        )
        conflicts |= any(not x.get("installed", True) for x in result.get("skills", {}).values())
        if conflicts:
            sys.exit(2)
    except (
        Fault,
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        zipfile.BadZipFile,
    ) as exc:
        result = (
            exc.json() if isinstance(exc, Fault) else {"code": "SETUP_ERROR", "message": str(exc)}
        )
        print(json.dumps({"ok": False, "error": result}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
