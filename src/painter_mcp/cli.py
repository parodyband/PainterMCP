"""Install, diagnose or serve Painter MCP."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .client import Client
from .common import Fault, connection_path


def main():
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
    for action in ("repair", "update"):
        command = commands.add_parser(
            action, help="Synchronize managed files with this installed package version"
        )
        command.add_argument("--replace-edited", action="store_true")
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
        elif arguments.command in ("repair", "update"):
            from .install import repair

            result = repair(arguments.replace_edited)
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
                    "Client/plugin version mismatch. Run painter-mcp update and restart Painter."
                )
        print(json.dumps(result, indent=2))
        conflicts = result.get("installed") is False or any(
            not x.get("configured", True) for x in result.get("clients", {}).values()
        )
        conflicts |= any(not x.get("installed", True) for x in result.get("skills", {}).values())
        if conflicts:
            sys.exit(2)
    except (Fault, OSError, ValueError) as exc:
        result = (
            exc.json() if isinstance(exc, Fault) else {"code": "SETUP_ERROR", "message": str(exc)}
        )
        print(json.dumps({"ok": False, "error": result}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
