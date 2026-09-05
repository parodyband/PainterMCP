"""Managed local installation with content hashes, conflict staging and reversible config edits."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .common import Fault, atomic_write, dumps, home


def sha(content):
    return hashlib.sha256(content if isinstance(content, bytes) else content.encode()).hexdigest()


def default_python_root():
    if platform.system() == "Windows":
        # SHGetFolderPathW honors redirected Documents (including OneDrive).
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        windows_loader = getattr(ctypes, "windll", None)
        if windows_loader is None:
            raise OSError("Windows document-directory API is unavailable; use --painter-python")
        result = windows_loader.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
        documents = Path(buffer.value) if result == 0 else Path.home() / "Documents"
        return documents / "Adobe" / "Adobe Substance 3D Painter" / "python"
    if platform.system() == "Darwin":
        return Path.home() / "Documents/Adobe/Adobe Substance 3D Painter/python"
    return Path.home() / "Documents/Adobe/Adobe Substance 3D Painter/python"


def backup(path):
    target = path.with_name(path.name + ".painter-mcp-backup-" + str(time.time_ns()))
    shutil.copy2(path, target)
    return target


def managed_files(files, manifest_path, replace_edited=False):
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"files": {}}
    )
    old = manifest.get("files", {})
    conflicts = []
    for path, content in files.items():
        if path.is_symlink():
            raise Fault("INSTALL_SYMLINK", f"Refusing to replace symlink {path}")
        if path.exists():
            current = sha(path.read_bytes())
            if current not in (sha(content), old.get(str(path))):
                conflicts.append(path)
    if conflicts and not replace_edited:
        staged = []
        for path, content in files.items():
            incoming = path.with_name(path.name + f".incoming-{__version__}")
            atomic_write(incoming, content)
            staged.append(str(incoming))
        return {
            "installed": False,
            "conflicts": [str(p) for p in conflicts],
            "staged": staged,
            "hint": "Merge your edits, or use --replace-edited to back up and replace managed files",
        }
    for path, content in files.items():
        if path in conflicts:
            backup(path)
        atomic_write(path, content)
        old[str(path)] = sha(content)
    atomic_write(manifest_path, dumps({"version": __version__, "files": old}))
    return {"installed": True, "files": len(files), "conflicts": []}


def configure_client(client, user_home, python, manifest, replace_edited=False):
    import tomlkit

    command: dict = {"command": str(python), "args": ["-m", "painter_mcp", "serve"]}
    connection_override = os.environ.get("PAINTER_MCP_CONNECTION")
    runtime_home = os.environ.get("PAINTER_MCP_HOME")
    if connection_override:
        command["env"] = {"PAINTER_MCP_CONNECTION": connection_override}
    elif runtime_home:
        command["env"] = {"PAINTER_MCP_HOME": runtime_home}
    if client == "codex":
        path = user_home / ".codex/config.toml"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        config = tomlkit.parse(text)
        section = config.setdefault("mcp_servers", tomlkit.table())
    else:
        path = user_home / ".claude.json"
        text = path.read_text(encoding="utf-8") if path.exists() else "{}"
        config = json.loads(text)
        section = config.setdefault("mcpServers", {})
        command["type"] = "stdio"
    current = section.get("painter", None)
    current_plain = json.loads(json.dumps(current)) if current is not None else None
    old = manifest.get(client)
    if (
        current_plain is not None
        and current_plain != command
        and current_plain != old
        and not replace_edited
    ):
        incoming = path.with_name(path.name + ".painter-mcp.incoming.json")
        atomic_write(incoming, json.dumps({"painter": command}, indent=2))
        return {"configured": False, "conflict": str(path), "proposed_entry": str(incoming)}
    if path.exists() and current_plain != command:
        backup(path)
    section["painter"] = command
    # Catch concurrent client edits before writing a shared configuration file.
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise Fault(
            "CONFIG_CHANGED", f"Client changed {path} during setup; retry with the client closed"
        )
    atomic_write(
        path,
        tomlkit.dumps(config) if client == "codex" else json.dumps(config, indent=2) + "\n",
        private=True,
    )
    manifest[client] = command
    return {"configured": True, "path": str(path)}


def install(painter_python=None, clients=(), user_home=None, replace_edited=False):
    root = Path(painter_python or default_python_root()).resolve()
    user_home = Path(user_home or Path.home()).resolve()
    package = Path(__file__).parent
    files = {
        root / "modules/painter_mcp" / p.name: p.read_text(encoding="utf-8")
        for p in package.glob("*.py")
    }
    files[root / "startup/painter_mcp_startup.py"] = (
        '"""Managed Painter MCP startup entry point."""\n'
        "from painter_mcp.plugin import close_plugin as close_plugin\n"
        "from painter_mcp.plugin import start_plugin as start_plugin\n"
    )
    manifest_path = root / "painter-mcp-install.json"
    result = managed_files(files, manifest_path, replace_edited)
    result["painter_python"] = str(root)
    result["restart_required"] = True
    if not result["installed"]:
        return result
    config_manifest_path = home() / "clients-install.json"
    config_manifest = (
        json.loads(config_manifest_path.read_text(encoding="utf-8"))
        if config_manifest_path.exists()
        else {}
    )
    result["clients"] = {}
    result["skills"] = {}
    for client in clients:
        result["clients"][client] = configure_client(
            client, user_home, Path(sys.executable), config_manifest, replace_edited
        )
        skill_root = user_home / (
            ".agents/skills/painter-mcp" if client == "codex" else ".claude/skills/painter-mcp"
        )
        try:
            skill = (
                importlib.resources.files("painter_mcp")
                .joinpath("data/SKILL.md")
                .read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            skill = (package.parent.parent / "skills/painter-mcp/SKILL.md").read_text(
                encoding="utf-8"
            )
        result["skills"][client] = managed_files(
            {skill_root / "SKILL.md": skill}, skill_root / ".managed.json", replace_edited
        )
    atomic_write(config_manifest_path, dumps(config_manifest), private=True)
    atomic_write(
        home() / "installation.json",
        dumps(
            {
                "version": __version__,
                "painter_python": str(root),
                "user_home": str(user_home),
                "clients": list(clients),
                "python": sys.executable,
            }
        ),
        private=True,
    )
    return result


def repair(replace_edited=False):
    path = home() / "installation.json"
    if not path.exists():
        raise Fault("NOT_INSTALLED", "Run painter-mcp install first")
    config = json.loads(path.read_text(encoding="utf-8"))
    return install(config["painter_python"], config["clients"], config["user_home"], replace_edited)


def remove_managed(manifest_path, root):
    removed: list[str] = []
    preserved: list[str] = []
    if not manifest_path.exists():
        return removed, preserved
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = Path(name)
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
            preserved.append(name)
            continue
        if path.exists():
            if sha(path.read_bytes()) == expected:
                path.unlink()
                removed.append(name)
            else:
                preserved.append(name)
    if not preserved:
        manifest_path.unlink()
    return removed, preserved


def uninstall():
    import tomlkit

    path = home() / "installation.json"
    if not path.exists():
        raise Fault("NOT_INSTALLED", "No managed installation found")
    config = json.loads(path.read_text(encoding="utf-8"))
    root = Path(config["painter_python"])
    removed, preserved = remove_managed(root / "painter-mcp-install.json", root)
    client_manifest_path = home() / "clients-install.json"
    manifest = (
        json.loads(client_manifest_path.read_text(encoding="utf-8"))
        if client_manifest_path.exists()
        else {}
    )
    user_home = Path(config["user_home"])
    for client in config["clients"]:
        skill_root = user_home / (
            ".agents/skills/painter-mcp" if client == "codex" else ".claude/skills/painter-mcp"
        )
        gone, kept = remove_managed(skill_root / ".managed.json", skill_root)
        removed += gone
        preserved += kept
        config_path = user_home / (".codex/config.toml" if client == "codex" else ".claude.json")
        if not config_path.exists():
            continue
        text = config_path.read_text(encoding="utf-8")
        document = tomlkit.parse(text) if client == "codex" else json.loads(text)
        section = document.get("mcp_servers" if client == "codex" else "mcpServers", {})
        if section.get("painter") == manifest.get(client) and "painter" in section:
            backup(config_path)
            del section["painter"]
            if config_path.read_text(encoding="utf-8") != text:
                raise Fault("CONFIG_CHANGED", "Client configuration changed during uninstall")
            atomic_write(
                config_path,
                tomlkit.dumps(document)
                if client == "codex"
                else json.dumps(document, indent=2) + "\n",
                private=True,
            )
        elif "painter" in section:
            preserved.append(str(config_path) + " painter entry")
    return {"removed": removed, "preserved_user_edits": preserved, "restart_required": True}
