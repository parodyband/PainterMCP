# Installation, updates and repair

## Requirements

- Licensed Substance 3D Painter with public Python plugins and PySide6. Painter
  12.1.4 on Windows is the live-validated version. Other versions/platforms must
  check `painter_status` and `api.inspect`; hosted OS tests do not certify Painter.
- A separate Python 3.10+ for the MCP stdio process. Do not install MCP dependencies
  into Adobe's bundled interpreter.
- Codex or Claude Code with local stdio MCP support. No Adobe cloud API key is needed.

## Windows setup

From the repository:

```powershell
git clone https://github.com/parodyband/PainterMCP.git
cd PainterMCP
.\scripts\install.ps1 -Clients codex,claude
```

The script creates `~/.painter-mcp/venv`, installs this package and its external
dependencies, copies the dependency-free plugin into Painter's user Python
directory, and configures the selected clients. It does not download Painter or
change licensing, firewall rules or system environment variables.

For a downloaded, extracted installer archive, run `./install.ps1` from its root.
The archive contains a matching wheel, manifest, checksums, this documentation and
the companion skill. No release is published until a maintainer pushes a version
tag. Until then, install from the repository or locally built wheel.

Start or restart Painter after installation:

```powershell
& "$env:USERPROFILE\.painter-mcp\venv\Scripts\painter-mcp.exe" doctor
```

Close/reopen your MCP client to discover `painter`. The plugin is loaded from
Painter's `python/startup` directory. The **Python** menu and application log show
plugin load failures. Painter installed through Steam may restart through Steam
and discard environment overrides supplied to an initially launched executable;
the default user-directory installation avoids relying on those overrides.

## Python and other platforms

```sh
python3 -m venv ~/.painter-mcp/venv
~/.painter-mcp/venv/bin/python -m pip install .
~/.painter-mcp/venv/bin/painter-mcp install --clients codex claude
```

`scripts/install.sh` performs these steps. Choose one client with
`painter-mcp install --clients codex` or `--clients claude`. A virtual environment
keeps this server independent of your project dependencies. Linux/macOS package,
stdio and setup contracts are tested in CI; a licensed app on those platforms has
not been live-validated here.

Override Painter's Python directory if your installation uses another location:

```sh
painter-mcp install --painter-python /absolute/custom/painter-python --clients codex
```

The directory receives `modules/painter_mcp` and `startup/painter_mcp_startup.py`.
Point Painter's documented `SUBSTANCE_PAINTER_PLUGINS_PATH` at the parent directory
in that application's environment. For manually loading inside an already running
Painter Python console, after installing to its normal user Python directory:

```python
import painter_mcp.plugin

painter_mcp.plugin.start_plugin()
```

Use `painter_mcp.plugin.close_plugin()` to stop it. Production updates should restart
Painter instead of reloading selected modules in a live interpreter.

## Client configuration

The installer changes only the server entry named `painter`:

- Codex: `~/.codex/config.toml`, `[mcp_servers.painter]`; skill at
  `~/.agents/skills/painter-mcp/SKILL.md`.
- Claude Code: user-scoped `~/.claude.json`, `mcpServers.painter`; skill at
  `~/.claude/skills/painter-mcp/SKILL.md`.

The command is the installation virtual environment's absolute Python executable,
with `args = ["-m", "painter_mcp", "serve"]`. It works without a particular working
directory. The official configuration references are
[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude Code MCP](https://code.claude.com/docs/en/mcp), and
[Claude Code skills](https://code.claude.com/docs/en/skills).

Existing unrelated entries and Codex TOML comments are preserved. A conflicting
unmanaged `painter` entry is not overwritten. Setup writes a proposed entry next
to the original configuration and returns a nonzero status for review.
If you intentionally use custom client configuration homes, use the emitted
entry or the clients' native MCP commands to place it there; `--user-home` changes
the parent used by the installer for both standard client paths.

## Discovery and credentials

The plugin binds only `127.0.0.1`, chooses an ephemeral port, and creates a fresh
random bearer credential per startup. Discovery is in
`~/.painter-mcp/connection.json`. The file is user-only on POSIX (`0600`) and gets
an explicit current-user ACL on Windows before the credential is written.
Do not share this file; it authorizes code execution inside Painter.

`PAINTER_MCP_HOME` overrides the state directory. `PAINTER_MCP_CONNECTION` overrides
the exact discovery file for separate instances. Set the same override for Painter
and the MCP client. Local setup can copy an override into the client entry, but
the bearer token itself is never copied there. No credentials are committed.

## Managed updates

Install the new source version or wheel into the same external virtual environment:

```powershell
& "$env:USERPROFILE\.painter-mcp\venv\Scripts\python.exe" -m pip install --upgrade .
& "$env:USERPROFILE\.painter-mcp\venv\Scripts\painter-mcp.exe" update
```

Then restart Painter and your MCP client. `update` synchronizes the installed
package's files; it does not execute a remote “latest” updater or silently fetch
unreviewed code. `doctor` warns if external and embedded package versions differ.

Every managed file has a stored hash. Missing/unchanged files are updated. If any
plugin file has user edits, the complete incoming plugin is staged as
`*.incoming-VERSION`, and the active plugin files are left in place. Skills have
the same preservation policy. Merge your changes, or explicitly use
`--replace-edited` to back up and replace them. Backups are timestamped sibling
files. Unknown extra files are not deleted. Restart after the complete plugin
update; do not load a mix of old/new modules into a running process.

`painter-mcp repair` restores missing managed files and checks configuration using
the same preservation rules. `painter-mcp uninstall` removes only unchanged managed
plugin/skill files and unchanged `painter` client entries. It preserves user edits,
backups, application projects and the external venv. Stop/restart Painter to unload
an installed plugin; deleting files cannot undo code already loaded into memory.

## Diagnostics

| Report | Action |
|---|---|
| `NOT_CONNECTED` | Start Painter and check the Python plugin log; verify your Python directory and discovery override. |
| `CONNECTION_LOST` | Painter may have closed or be unresponsive. Recover the reported request ID before repeating any edit. |
| `AUTH_REQUIRED` | Discovery credential is stale; restart the plugin and retry `doctor`. Do not paste tokens into client config. |
| `RUNTIME_CHANGED` | Recovery belongs to an old process. Observe the project before deciding whether another edit is needed. |
| `QUEUE_FULL` / `RECOVERY_CAPACITY` | Finish/recover outstanding work. At ID capacity, restart only after outcomes are known. |
| Old `main_thread_last_seen_seconds` | Application event loop is blocked, a modal/native operation is active, or code holds the GIL. Transport status may still work. |
| `UNSAVED_PROJECT` | Save first; discard only when intentionally authorized. |
| `UNSUPPORTED_API` | Inspect the runtime API/version. No automatic UI edit fallback is attempted. |
| `CAPTURE_UNAVAILABLE` | For window capture, restore Painter's visible window. For channel previews, choose an existing channel/stack. |
| `scope_required:true` | There is no active painting stack, often in Baking mode. Pass a texture set explicitly or switch to `Edition`. |
| Installation conflicts | Review incoming files or explicitly back up/replace with `--replace-edited`. |

Do not open the loopback listener to a LAN, reverse proxy or browser origin. The
external MCP client disables HTTP proxy use for its local connection.
