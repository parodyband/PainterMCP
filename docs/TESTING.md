# Tests, CI and release procedure

## Automated without Painter

```sh
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=painter_mcp
uv run python -m build
uv run python scripts/package.py
uv run python scripts/check_package.py
uv run python scripts/smoke_install.py
uv run python scripts/smoke_update.py
```

The ordinary suite uses an explicit application test double to validate engine
contracts. Transport tests use real sockets, and protocol tests launch the actual
stdio server and connect with the official MCP client SDK. Lifecycle tests verify
timers, event callbacks, sockets and discovery cleanup through a Qt boundary fake.
Adapter regressions reproduce binding details discovered in the licensed app.
The installation smoke test installs the built wheel in a fresh virtual
environment, configures both clients under an isolated user directory, repairs a
missing plugin file and uninstalls managed files.

`.github/workflows/ci.yml` runs on Linux (Python 3.10/3.13), Windows (3.13), and
macOS (3.13). It validates lint, formatting, static checks, tests, wheel/sdist,
installer archive, manifest/hash/version consistency and clean wheel installation.
Hosted runners do not contain Painter. A skipped licensed test is not evidence of
application validation. Coverage reports include the unexecuted Adobe adapter;
subprocess and licensed-app coverage must not be inferred from unit coverage.

## Licensed acceptance

Prepare a disposable desktop Painter session with a valid license and the matching
plugin installed. Close existing projects yourself; the suite refuses to replace
one. Set the explicit isolation gate:

```powershell
$env:PAINTER_MCP_TEST_ISOLATED = '1'
uv run pytest tests/licensed
```

Or run `uv run python scripts/validate_painter.py` for the same suite and a concise
report path. The suite creates original mesh fixtures, marks its project with a
unique ownership value, and closes only its own project in cleanup. It writes
SPPs, images, textures and reports under `.local/live-validation/`, never to the
repository's source or an existing user project. Imports use project/session
resources; smart material/mask files are exported into the isolated directory.
The shelf-registration check uses a fresh empty directory and removes its own
registration before opening a project.

The suite exercises all specialist operation names, with assertions covering
native texture output, groups/effects/materials/masks, baking completion, settings,
save/copy/reopen, asynchronous mesh reload, identity, sessions, stale guards and
duplicate recovery. It also uses a two-tile UDIM project for tile resolution,
geometry masks and explicit preview truncation. Not every native enum value,
commercial material, renderer or GPU is covered. See [validation](VALIDATION.md).

The workflow `.github/workflows/licensed-painter.yml` is separately gated by:

1. Manual `workflow_dispatch` with explicit isolation confirmation.
2. Repository variable `ENABLE_LICENSED_PAINTER_TESTS=true` (default **false**).
3. Protected `licensed-painter` environment with owner review and `main` branch restriction.
4. A self-hosted Windows x64 runner labelled `painter-licensed`, running in an
   interactive logged-in desktop with the prepared matching plugin and license.

No such runner is provisioned by this repository. The operator installs/restarts
the matching plugin before dispatch. The workflow does not update a live artist's
plugin, start an unauthorized license session, or upload SPPs/screenshots. It uploads
only the JUnit and sanitized numerical acceptance report. Review trusted source
before enabling this runner; do not run pull-request workflows on it.

## Releases

The package version is `1.1.0` in `pyproject.toml`, `painter_mcp.__version__` and
the companion skill metadata. `check_package.py` checks consistency, artifact
contents, installer manifest and checksums. It optionally checks an exact tag:

```sh
uv run python scripts/check_package.py --tag v1.1.0
```

For a future release, update those versions together, update the changelog,
validate licensed compatibility when needed, then let CI pass on `main`.
Only then create and push the matching `vVERSION` tag. The tag workflow rebuilds
and retests, rejects mismatched tags, and publishes the wheel, source archive,
portable installer archive, release-manifest.json and SHA256SUMS in a separate minimal-permission job.
It does not publish to PyPI. No tag or release is needed for development CI.

The initial delivery deliberately creates **no release tag and no release assets**.
Tag-driven publishing configuration is checked, while the actual publication
step remains unexecuted until a maintainer chooses to release.
