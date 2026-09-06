# Installation, updates and repair

## Requirements

- Licensed Substance 3D Painter with public Python plugins and PySide6. Painter
  12.1.4 on Windows is the live-validated version. Other versions/platforms must
  check `painter_status` and `api.inspect`; hosted OS tests do not certify Painter.
- A separate Python 3.10+ for the MCP stdio process. The double-click Windows installer
  provisions a private runtime automatically if a suitable interpreter is unavailable.
  Do not install MCP dependencies into Adobe's bundled interpreter.
- Codex or Claude Code with local stdio MCP support. No Adobe cloud API key is needed.

## Windows setup

Download `Install-PainterMcp.cmd` from the latest GitHub release and double-click it.
The same launcher is included in the ZIP. It works both after extraction and when
Windows Explorer extracts only that file from inside the ZIP. The latter path
downloads the exact matching release and verifies it before starting setup.

When no suitable 64-bit Python is found, setup downloads the official CPython
3.13.15 NuGet runtime and checks its pinned SHA-256. It is installed privately under
`~/.painter-mcp/runtimes/`, without modifying system Python, PATH, registry entries or
file associations. The Python runtime is retained because the MCP venv depends on it.
The [official Python Windows documentation](https://docs.python.org/3.13/using/windows.html#the-nuget-org-packages)
describes this side-by-side distribution. Internet access is needed for downloads
and Python dependencies. Windows may show its normal downloaded-script warning;
the installer changes execution policy only for its own PowerShell process.

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

For a downloaded, extracted installer archive, double-click `Install-PainterMcp.cmd`.
The archive contains a matching wheel, manifest, checksums, this documentation and
the companion skill. The console remains open after success or failure so the
result can be read. Existing modified plugin and skill files remain protected.

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
with arguments pointing to `~/.painter-mcp/launch.py` and `serve`. That stable launcher
selects the version activated by Painter's startup loader. It works without a particular working
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

Painter checks GitHub for stable releases once per day and offers installation.
Use **Help → Check Painter MCP Updates…**, or:

```powershell
& "$env:USERPROFILE\.painter-mcp\venv\Scripts\painter-mcp.exe" update
```

The selected release is verified and staged in its own directory and Python
environment. Restart Painter, then the MCP client to activate it. `update --check`
only checks metadata; `update-status` reports pending/active versions, and `rollback`
stages the previous version. See [update guarantees and recovery](UPDATES.md).

For an explicit source/wheel upgrade, run the normal installer again. The older
1.0.0 installation needs that one-time upgrade to obtain the new updater.

Every managed file has a stored hash. Missing/unchanged files are updated. If any
plugin file has user edits, the complete incoming plugin is staged as
`*.incoming-VERSION`, and the active plugin files are left in place. Skills have
the same preservation policy. Merge your changes, or explicitly use
`--replace-edited` to back up and replace them. Backups are timestamped sibling
files. Unknown extra files are not deleted. Restart after the complete plugin
update; do not load a mix of old/new modules into a running process.

`painter-mcp repair` restores missing managed files using
the same preservation rules. Versioned plugin repairs use the cached verified wheel.
Rerun the full installer to repair a broken client environment or stable launcher.
`painter-mcp uninstall` removes only unchanged managed
plugin/skill files and unchanged `painter` client entries. It preserves user edits,
backups, application projects and the external venv. Stop/restart Painter to unload
an installed plugin; deleting files cannot undo code already loaded into memory.

## Diagnostics

For unattended testing, set `PAINTER_MCP_INSTALLER_NO_PAUSE=1`. Optional overrides are
`PAINTER_MCP_INSTALLER_ROOT` (venv), `PAINTER_MCP_INSTALLER_PAINTER_PYTHON`,
`PAINTER_MCP_INSTALLER_USER_HOME`, and comma-separated `PAINTER_MCP_INSTALLER_CLIENTS`.
`PAINTER_MCP_HOME` isolates all runtime state. A supplied offline/test ZIP requires
both `PAINTER_MCP_INSTALLER_ARCHIVE` and `PAINTER_MCP_INSTALLER_SHA256`; the package's
internal manifest is still verified. `PAINTER_MCP_INSTALLER_FORCE_PRIVATE_PYTHON=1`
exercises prerequisite provisioning for a fresh installation even if Python is installed.

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
