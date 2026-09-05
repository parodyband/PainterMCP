# Validation results

Validated locally on September 5, 2026 using Windows, Steam Painter **12.1.4**,
Painter Python **3.13.9** and PySide6. The external development interpreter was
Python **3.12.9**. The application was licensed and available; no licensing bypass,
hosted-runner Painter installation or fake application success was used.

## Licensed application results

The expanded acceptance run passed **133 requests**, exercising **all 45 specialist
operation names**, plus combined observation, scoped changes, Python/JavaScript
scripts and sessions. It created isolated original cube and two-UDIM projects and
closed them after checking project ownership. Existing user projects were not opened,
saved, replaced or discarded.

Live assertions covered:

- Project creation settings, save/copy/close/reopen and asynchronous mesh reload.
- Texture-set identity through rename, stack activation, channel add/edit/remove,
  texture-set and per-UV-tile resolution.
- Groups, paint/fill layers, instances, selection, opacity/blending, effects,
  masks, mask fills, smart materials/masks and mesh/UV-tile geometry masks.
- Uniform colors, bitmap/procedural sources, parameter edits, UV/triplanar
  projection, symmetry availability, nested UV scale and effect gamma.
- Project/session resource import, replacement, paginated search and shelf
  registration/removal with no project open.
- Camera properties, native channel previews, Qt window capture, texture export
  planning/output, export presets and mesh export.
- Native baking property discovery, normal/AO baking, app-job completion and
  mesh-map assignment. The cancellation call was exercised on a completed bake;
  stopping a long in-progress bake remains a separate manual timing case.
- Stale-state rejection, accurate partial failure, persistent SDK values,
  JavaScript, duplicate request recovery and observation outside the active
  painting workspace.

The returned window capture was visually inspected: it contained Painter's 3D
cube viewport, 2D texture view, layer stack and surrounding UI. The base-color
texture preview was also inspected. The UDIM preview reported two tiles and
explicitly returned only the first. These images and generated project/material
files remain local test artifacts and are not shipped.

The managed installer was also run against the local clients. Claude Code reported
the stdio server connected. The Codex server entry was verified with its CLI using
a temporary `-c features.context_management=false` override: the user's existing
`features.context_management` table is incompatible with their installed Codex CLI
0.147.0. Comparison with the installer backup confirmed that setting was unchanged.
No unrelated client settings were modified. Restart the Codex app to load the server.

## Measured agent-loop cost

The measured workload renames one layer eight times and observes it. The separate
path uses normal per-operation undo and full operation results. The combined path
uses a layerstack undo group, suppressed intermediate outputs and post-observation.
Both finish with the same final name. Measurements include loopback transport and
Qt scheduling, exclude model inference/network-to-model time, and are not a GPU
performance guarantee.

| Path | Bridge round trips | Returned bytes | Elapsed time |
|---|---:|---:|---:|
| Eight separate edits plus observation | 9 | 24,702 | 400.661 ms |
| One grouped batch with selected outputs and observation | 1 | 8,002 | 31.142 ms |

For the entire 133-request run: **39.2 ms median request latency**, **133 bridge
round trips**, and **478,552 response bytes**. Cold startup, library crawling, large
assets, GPU load and application dialogs change these numbers. The suite writes
its own timing, operation coverage, error expectations and response-size report.

## Automated and manual boundaries

The Painter-free suite validates real stdio protocol exchange, authenticated
loopback HTTP, concurrent duplicate submission, recovery expiry/tombstones,
busy-state controls, partial failure, output limits, lifecycle cleanup, installation
conflicts, repair and wheel installation. Static checks cover the complete Python
package; unavailable Adobe/PySide type imports are treated dynamically.

Normal hosted CI deliberately skips licensed acceptance. Its Linux/macOS/Windows
results certify the package and bridge contracts, not Painter's behavior on those
OSes. Coverage statistics from those runs do not measure Adobe's native engine or
the live application process.

Remaining platform/API validation limits:

- Other Painter versions, macOS/Linux Painter, different GPUs and large production
  projects have not been live-validated here.
- Iray rendering completion, all shader/resource types, all baking properties,
  per-baker UV combinations and arbitrary public escape-hatch calls cannot be
  exhaustively certified by the fixture suite.
- Cooperative cancellation while a long native bake is actively running requires
  timing-sensitive manual validation; no immediate-stop claim is made.
- Undo grouping is entered/exited in live tests and grounded in Adobe's public
  `ScopedModification` contract. A human UI undo/redo inspection is separate;
  no programmatic undo/atomic rollback API is invented.
- A process kill during a native edit loses in-memory recovery. Manual crash/restart
  recovery requires observing saved/current state before retrying. Unit tests cover
  stale runtime IDs, memory expiry and orderly shutdown, not every crash point.
- Brush painting, independent layer duplication/reordering, complete document
  configuration editing and precise framebuffer capture remain API limitations.

See [capability matrix](CAPABILITIES.md) for per-workflow implementation and test
mapping, and [testing](TESTING.md) for reproducing the gated acceptance run.
