# Observe, act, verify and recover

Start with `painter_status`. It reports the plugin runtime ID, Painter/Python
versions, operation availability and recovery capacity. Only eight tools are
advertised by default. `painter_describe` without arguments lists domains; request
`{"domain":"baking"}` or `{"names":["layers.create","sources.set"]}` for schemas.
The live schema and `api.inspect` are authoritative for the installed version.

## One edit and its evidence

```json
{"texture_set":"Body","image":"texture","channel":"BaseColor","width":768,"limit":30}
```

Use the returned `observation_id` for an edit that depends on those facts.
The example below assumes a texture set named `Body` exists:

```json
{
  "request_id":"CURRENT_RUNTIME_ID:body-basecoat-001",
  "if_observation":"RETURNED_OBSERVATION_ID",
  "steps":[
    {"id":"layer","op":"layers.create","args":{"kind":"fill","name":"Base coat","position":{"texture_set":"Body"}},"select":["/ref"]},
    {"id":"color","op":"sources.set","args":{"node":{"$ref":"layer#/ref"},"channel":"BaseColor","color":[0.12,0.3,0.6]},"select":[]}
  ],
  "undo":"layerstack",
  "observe":{"texture_set":"Body","image":"texture","channel":"BaseColor"}
}
```

`$ref` points into the full earlier operation result. It is independent of `select`.
JSON Pointer escaping is supported: `~1` for `/`, `~0` for `~`. Forward references,
duplicate step IDs, invalid arguments and unsupported grouped undo are rejected
before editing. Selection pointers are evaluated after the operation; a missing
selected field can therefore produce a presentation error after a successful edit.

The result contains `steps`, `undo`, and optional `observation`. Images appear in
MCP content; `observation_image_indices` locates them. A capture failure remains
in `observation.data.image.error` while structural facts and successful edits are
retained. Texture preview currently returns one channel and the first tile, with
explicit tile truncation. The image is not a material render.

## App jobs and retries

`wait_ms` defaults to 1,000 ms and is capped at 5,000. Use `wait_ms:0` for immediate
acknowledgement. Queued/running receipts are not completed edits. Poll
`painter_request` using `request_id`, allowing time between polls. Terminal
responses contain the original execution result, its `isError`, and request
metadata. Images remain native MCP image blocks on recovery.

Repeat the same request ID only with identical logical arguments. Different
arguments require a new ID. If a result expired, the receipt says
`result_expired:true`; the action remains deduplicated. If the runtime restarted,
inspect the actual project before deciding whether to submit new work.

Baking and mesh reload expose a second level of state: `baking.start` or
`project.reload_mesh` completes when launch succeeds and returns `job_id`.
`job.status` reports the app operation's actual outcome. `job.cancel` requests
cooperative baking cancellation; it does not forcibly stop Painter. A completed
bake leaves Painter in Baking mode. Use `viewport.set` with `mode:"Edition"`
before relying on the painting workspace's active stack. Pass explicit texture-set
references when reading scoped data outside that workspace.

## Persistent Python

Open `painter_session` with `{"action":"open"}`. Then:

```json
{
  "session_id":"RETURNED_SESSION_ID",
  "source":"layer = painter.call('layers.create', {'kind':'fill','name':'From Python'})\nhandle = painter.node(layer['ref'])\nresult = {'ref':handle.ref, 'name':handle.name}",
  "observe":{"image":"none"}
}
```

Python helpers persist in that namespace. Each cell replaces `arguments` and the
`painter` SDK, clears old `result`, and captures up to 8,192 stdout/stderr characters.
Use the current cell's SDK; calling a retained SDK fails with `STALE_CELL`.
`painter.call` permits 128 calls per cell. `keep/get/release` stores up to 64 JSON
results / 4 MB per session. Arbitrary Python namespace memory is not bounded.

There are 16 sessions, with 30-minute idle expiry. Reset/close explicitly with
`painter_session`, or rebuild after project replacement. Scripts run with the
same host privileges as other Painter plugins. They are not sandboxes or workers.

## General API access

`api.inspect` lists public names, signatures, enum members and bounded docstrings.
For example, inspect `textureset.ChannelType`, `layerstack.ProjectionMode` or
`project.Settings`. `api.call` can invoke a public function, get a property, set a
property or call a method on a returned handle:

```json
{"steps":[{"id":"version","op":"api.call","args":{"path":"application.version"}}]}
```

Arguments support `{"$enum":"project.NormalMapFormat.OpenGL"}`,
`{"$handle":"RETURNED_REFERENCE"}`, and native constructors such as
`{"$type":"textureset.Resolution","args":[512,512]}`. Use `kwargs` for named
constructor fields. Complex callable callbacks or Python algorithms belong in
`painter_script`, not invented JSON encodings. JavaScript scripts receive
function-local `arguments` and return the variable `result` through Adobe's
`substance_painter.js.evaluate` bridge; JavaScript sessions are not persistent.

## Scoped verification

`painter_changes` accepts an observation's `cursor`. It compares only returned
covered facts. `requires_observation:true` means refresh and reconsider. It does
not detect every intermediate edit or pixel change. `layers.list`, resource
search, API discovery and other large listings have snapshot `next_cursor`
pagination. Do not replace a continuation with a new offset and assume the same
snapshot. Read `truncated`, error and undo fields before continuing a partial task.
