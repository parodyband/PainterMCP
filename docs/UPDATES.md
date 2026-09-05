# GitHub release updates

Starting with 1.1.0, Painter MCP matches Maya-MCP's update workflow: it checks for
a newer stable GitHub release in the background, offers **Install Update**, **Later**
or **View Release**, and prepares the chosen release alongside the running version.
It does not silently install or reload code into the current project.

## Using it

Restart Painter once after installing 1.1.0. Automatic checks are limited to once
per 24 hours; the application periodically checks whether that interval has elapsed.
Use **Help → Check Painter MCP Updates…** to check immediately. Set
`PAINTER_MCP_DISABLE_UPDATE_CHECK=1` in Painter's environment to disable automatic
checks while retaining the manual command. Offline/rate-limit failures are quiet
for automatic checks and shown for manual checks.

The CLI offers the same workflow:

```powershell
painter-mcp update --check
painter-mcp update
painter-mcp update-status
painter-mcp rollback
```

`update` downloads and stages the latest compatible stable release. `--check` only
checks metadata. Painter must be connected so its actual version can be checked.
`rollback` stages the previously active package. Restart Painter, then restart the
AI client so both load the selected version. Existing MCP connections continue
running their old process until they close. No project is saved, closed or discarded
by the updater.

The legacy 1.0.0 command did not download releases. Existing 1.0.0 installations
need the normal source/wheel installer once to get 1.1.0 and its stable launchers.
Subsequent published releases can use the built-in update flow.

## Trust and compatibility

Downloads are restricted to this repository's HTTPS GitHub release endpoints and
GitHub's release asset CDN. The updater verifies release/tag/manifest identity,
archive size and SHA-256, GitHub's asset digest when present, internal file hashes,
wheel identity and package version. It rejects unsafe archive paths, Windows path
aliases, symlinks, encrypted archives, duplicate names and excessive expanded sizes.
Downloads have byte and time limits. This trusts the repository's release publisher
and the Python dependency index; hashes are integrity checks, not independent signatures.

Painter has no Maya-style compiled API binary in this package. Instead, each release
declares a Painter version range, Python minimum, platform list and required public
API members. The initial range is Painter 12.1 through 12.x with Python 3.10+.
Activation rechecks Painter's version, Python version and required members inside
the real application. Linux/macOS preparation is CI-tested; actual Painter behavior
on those platforms still needs licensed validation.

## Preparation, restart and rollback

The stable `~/.painter-mcp/bootstrap.py` loader is used by Painter's startup plugin.
The stable `~/.painter-mcp/launch.py` client command selects the same active version.
New code and an independent Python virtual environment are prepared under
`~/.painter-mcp/versions/`. The original base environment remains available for the
first rollback. Existing version directories are never overwritten during an update.

Only after verification and an interpreter smoke check does the updater atomically
write `pending-update.json`. The next Painter startup validates that candidate and
switches `active-version.json`, preserving the previous pointer. Preparation and
activation share a process lock. A concurrent preparation leaves the current
version active for that launch. A failed activation records `activation-error.json`
and retains the current version; inspect `update-status` before retrying.

Shutdown signals the updater worker to stop and terminates only its own preparation
subprocess tree if necessary. Failed preparation never changes the active pointer.
An interrupted process may leave an unused staging directory, but without a ready
record and pending pointer it is not selected. Older directories are retained for
rollback and user-edit recovery; disk cleanup is manual.

## User edits and repair

Changed managed plugin files block automatic staging. Changes made after staging
also block activation. Restore/merge them or use explicit `repair --replace-edited`
to back up and replace files. Ordinary active-package user edits are not silently
overwritten. `repair` restores missing versioned plugin files from its cached verified
wheel; a damaged cached wheel requires restaging/reinstalling the package.

Only previously managed companion skills are updated. Edited skills are preserved
with an incoming file labelled with the new version. No new client is enrolled and
background updates do not rewrite client configuration. Skills can be updated during
staging; their runtime-capability checks remain important until the app restarts.

Explicitly running the source/wheel installer selects that base installation again
and clears pending/active update pointers. Retained versions stay on disk. This is
also the recovery path if a dependency environment or stable launcher is damaged.

## Publishing updates

`scripts/package.py` emits `release-manifest.json` alongside the ZIP, wheel, sdist
and SHA256SUMS. The tag workflow verifies these and exercises a real isolated update
installation before publishing. Users follow **published stable releases**, not
arbitrary commits on `main`; pushing source alone does not publish an update.

Before the first stable release is published, checks correctly report
“No stable release has been published yet.” To publish the current version, push the
matching `v1.1.0` tag after CI passes; future versions use the same workflow.
