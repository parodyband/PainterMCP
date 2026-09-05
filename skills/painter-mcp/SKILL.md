---
name: painter-mcp
description: Inspect and edit a live Adobe Substance 3D Painter project through Painter MCP, including layers, masks, materials, baking and texture export. Use for operating Painter, not ordinary development of the MCP repository.
metadata:
  version: "1.1.0"
---

# Work efficiently in Painter

Use the connected Painter MCP server within the user's existing task authorization.
Hosts may prefix tool names. Discover callable names; specialist operation names
inside batches and scripts use the canonical names returned by `painter_describe`.

Start with `painter_status` to detect the running version, capability gaps, runtime ID
and recovery limits. Fetch only needed domains or exact schemas with
`painter_describe`. Missing native APIs are limitations; do not invent replacements.

Use `painter_observe` for one texture set/stack. It combines project, channels,
layers, selection and an optional native MCP image. `image:"texture"` gives an
exported channel preview; `image:"window"` uses a Qt capture fallback with visible
UI and potentially stale GPU pixels. `image:"none"` avoids capture cost. Inspect
truncation and capture errors. A successful observation may still lack an image.
Pass returned `nodes` references to narrow layer facts. Selection is unavailable
for inactive stacks. After baking, use `viewport.set` with `mode:"Edition"` or
pass an explicit texture set to inspect outside the painting workspace.

Batch known operations with `painter_run`. Use `{"$ref":"stepId#/ref"}` to pass
an earlier result, and `select:["/ref"]` or `select:[]` to limit returned data.
References use full results even when outputs are selected. Include `observe`
for post-action evidence. Lifecycle operations may leave Painter busy; ungrouped
batches yield between steps until Painter is ready. A baking launch returns an
application `job_id`; query `job.status` for the actual outcome before using maps.

Use `if_observation` when edits depend on observed facts. The guard compares only
the recorded scope and fields at batch start. It does not cover brush pixels,
resource contents, shaders, unreturned nodes or changes between yielded steps.
On stale/expired state, observe and reconsider. `painter_changes` compares the
same scope without an image; it is not a complete event journal. Page through
`next_cursor`; expired pages require a fresh query.

Use returned node/texture-set references through renames. Project replacement
invalidates references, observations and Python sessions. Deleted nodes are errors.
Generic API object handles are bounded and may expire; reacquire them from owners.

For reusable algorithms, open `painter_session` with `action:"open"`, then pass
`session_id` to `painter_script`. Python variables persist. Pass inputs in
`arguments`, assign `result`, and use the current cell's `painter` SDK:

- `call(op,args)` executes a discovered operation and returns its full result.
- `observe(**options)` returns facts and emits an image.
- `node(ref)` creates a checked handle with live `.name` and `.ref`.
- `keep(json)`, `get(token)`, `release(token)` retain bounded JSON intermediates.

Use `api.inspect` to discover runtime public signatures/enums and `api.call` for
public APIs beyond typed workflows. Constructors use `$type` with `args`/`kwargs`,
enums use `$enum`, and object arguments use `$handle`. Python and JavaScript run
with full host privileges; they are not sandboxed, memory-bounded or force-cancellable.
Keep cells finite. Sessions expire after 30 idle minutes or project replacement.

For edits, choose `request_id` as the current `runtime_id` plus `:` and a unique
logical-action ID. Repeating identical arguments with the same ID recovers the
same outcome; different arguments conflict. Poll `painter_request` with time
between calls. Queued/running does not mean completed. Result recovery preserves
native image content and execution `isError`; inspect step states and job outcomes.
After connection loss, recover the original ID before retrying. Expired results
leave tombstones, so retries do not execute again. A runtime restart loses recovery
memory; inspect project state before deciding whether a new action is needed.

Failures can leave edits and files behind. Inspect completed, failed and skipped
steps. A selection/output or post-observation failure does not justify repeating
the edit. Grouped undo applies only to `undo:"layerstack"` batches with eligible
operations. Nothing promises atomicity, rollback or cancellation of running scripts.
Only queued requests can be cancelled; baking cancellation is cooperative.

Install this skill and client configuration with `painter-mcp install --clients
codex claude` (choose installed clients). Codex uses `~/.agents/skills/painter-mcp`;
Claude Code uses `~/.claude/skills/painter-mcp`. `painter-mcp update --check` checks
GitHub; `update` stages a verified stable release for the next Painter/client restart.
Painter also checks daily and has Help → Check Painter MCP Updates. `update-status`
reports pending activation, and `rollback` stages the previous version. Do not
restart or close a user's project without authorization. `repair` restores
missing managed files. Local edits are preserved with incoming files for review;
`--replace-edited` explicitly backs up and replaces them. Use `painter-mcp doctor`
for connection failures. Installation details are in the project's README.
