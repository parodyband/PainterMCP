import json
from pathlib import Path

import tomlkit

from painter_mcp.install import configure_client, install, managed_files, repair, uninstall


def test_install_repair_update_and_preserve_edits(tmp_path, monkeypatch):
    monkeypatch.setenv("PAINTER_MCP_HOME", str(tmp_path / "runtime"))
    root = tmp_path / "painter-python"
    user = tmp_path / "user"
    result = install(root, ["codex", "claude"], user)
    assert result["installed"]
    assert (root / "startup/painter_mcp_startup.py").exists()
    skill = user / ".agents/skills/painter-mcp/SKILL.md"
    assert skill.exists()
    missing = root / "modules/painter_mcp/adapter.py"
    missing.unlink()
    assert repair()["installed"]
    assert missing.exists()
    original = missing.read_text(encoding="utf-8")
    missing.write_text(original + "\n# User customization\n", encoding="utf-8")
    result = repair()
    assert not result["installed"]
    assert "# User customization" in missing.read_text(encoding="utf-8")
    assert missing.with_name("adapter.py.incoming-1.0.0").exists()
    result = repair(replace_edited=True)
    assert result["installed"]
    assert list(missing.parent.glob("adapter.py.painter-mcp-backup-*"))
    skill.write_text(skill.read_text(encoding="utf-8") + "\nPersonal note\n", encoding="utf-8")
    result = uninstall()
    assert not missing.exists()
    assert skill.exists()
    assert str(skill) in result["preserved_user_edits"]


def test_config_preserves_unrelated_entries_and_comments(tmp_path):
    codex = tmp_path / ".codex/config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        '# User comment\nmodel = "my-model"\n[mcp_servers.other]\ncommand="other"\n',
        encoding="utf-8",
    )
    manifest = {}
    assert configure_client("codex", tmp_path, Path("/python"), manifest)["configured"]
    config = tomlkit.parse(codex.read_text(encoding="utf-8"))
    assert config["model"] == "my-model"
    assert config["mcp_servers"]["other"]["command"] == "other"
    assert "# User comment" in codex.read_text(encoding="utf-8")
    config["mcp_servers"]["painter"]["args"] = ["user-edited"]
    codex.write_text(tomlkit.dumps(config), encoding="utf-8")
    assert not configure_client("codex", tmp_path, Path("/new-python"), manifest)["configured"]
    assert "user-edited" in codex.read_text(encoding="utf-8")


def test_first_install_existing_unmanaged_file_is_not_overwritten(tmp_path):
    file = tmp_path / "user.py"
    file.write_text("mine", encoding="utf-8")
    result = managed_files({file: "ours"}, tmp_path / "manifest.json")
    assert not result["installed"]
    assert file.read_text(encoding="utf-8") == "mine"


def test_claude_json_other_servers_preserved(tmp_path):
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "test"}}, "theme": "dark"}),
        encoding="utf-8",
    )
    configure_client("claude", tmp_path, Path("/python"), {})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "test"}
    assert data["mcpServers"]["painter"]["type"] == "stdio"


def test_uninstall_manifest_cannot_delete_outside_root(tmp_path, monkeypatch):
    from painter_mcp.install import remove_managed, sha

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "unrelated.txt"
    outside.write_text("keep", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"files": {str(outside): sha("keep")}}), encoding="utf-8")
    removed, preserved = remove_managed(manifest, root)
    assert not removed
    assert str(outside) in preserved
    assert outside.exists()
