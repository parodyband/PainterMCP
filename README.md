# Painter MCP

An agent-oriented Model Context Protocol server for Adobe Substance 3D Painter.
Eight default tools combine observation, batched actions, recovery and persistent
scripting. Specialist schemas cover the public Painter API without filling every
agent turn with dozens of tools.

Requires Python 3.10+ outside Painter and a licensed Painter installation with
Python plugins, PySide6 and the layerstack API. Development targets Painter 12.1.4.
Painter is an interactive desktop application; the server does not bypass licensing.

[CI](https://github.com/parodyband/PainterMCP/actions/workflows/ci.yml) checks the
bridge, protocol and installers on Windows, macOS and Linux. The licensed Windows
acceptance suite exercised all 45 specialist operations; see [measured results](docs/VALIDATION.md).

## Install from source

```powershell
git clone https://github.com/parodyband/PainterMCP.git
cd PainterMCP
.\scripts\install.ps1 -Clients codex,claude
```

Choose only the clients you use. Start or restart Painter, then run
`& "$env:USERPROFILE\.painter-mcp\venv\Scripts\painter-mcp.exe" doctor`.
Restart your MCP client to load `painter`.
The installer places the plugin in your Painter user Python directory and installs
the companion skill. Client configurations contain no bearer credentials.

For a custom Painter Python location use `--painter-python PATH`. For an isolated
launch, set `SUBSTANCE_PAINTER_PLUGINS_PATH` to that directory in the application
process environment. The directory contains `modules/` and `startup/`.

## Use

1. `painter_status` reports runtime/version/capabilities and request-ID prefix.
2. `painter_observe` returns combined facts and optional `image:"texture"` or
   `image:"window"`. Images are native MCP image content.
3. `painter_describe` with `domain:"layers"` returns specialist schemas.
4. `painter_run` batches operations; include `observe` to verify in the same result.

```json
{
  "steps": [
    {"id":"fill","op":"layers.create","args":{"kind":"fill","name":"Base coat"}},
    {"id":"color","op":"sources.set","args":{"node":{"$ref":"fill#/ref"},"channel":"BaseColor","color":[0.12,0.3,0.6]},"select":[]}
  ],
  "undo":"layerstack",
  "observe":{"image":"texture","channel":"BaseColor"}
}
```

For tracked edits, set `request_id` to the runtime ID from `painter_status`, `:`,
and your unique action ID. Recover with `painter_request`. Never blindly repeat
an edit after a connection failure. See [the agent loop](docs/AGENT_LOOP.md),
[capability matrix](docs/CAPABILITIES.md), [installation](docs/INSTALLATION.md),
[architecture](docs/ARCHITECTURE.md), and [validation](docs/VALIDATION.md).

## Development

```powershell
python -m pip install -e '.[dev]'
python -m pytest
python -m ruff check .
python -m mypy
python -m build
```

Hosted CI uses contract fakes and a real MCP protocol client; it has no licensed
Painter. Licensed tests require an explicitly isolated application instance.
See [testing and releases](docs/TESTING.md).

## Limits

No atomic transactions or forced cancellation of arbitrary scripts. Grouped undo
covers supported layerstack edits only. State guards compare returned facts, not
brush pixels or the complete evaluated document. Window capture is a Qt fallback;
texture previews use native texture export. Supported APIs do not expose arbitrary
brush strokes, general layer reordering/duplication, or every project dialog setting.
The public Python/JavaScript escape hatches preserve access to version-specific APIs.

MIT licensed. Unaffiliated with Adobe. Adobe application code, assets and credentials
are not included.
