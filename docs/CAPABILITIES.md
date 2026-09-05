# Verified capability matrix

Implementation target: Adobe Substance 3D Painter **12.1.4**, Steam for Windows,
Python **3.13.9**, PySide6. Verification used Adobe's installed public module
signatures and bundled Python/JavaScript reference, then a licensed live run.
No Adobe implementation, tests, asset files or documentation copies are shipped.

Adobe's [Python API site](https://adobedocs.github.io/painter-python-api/) explains
the integration and plugin lifecycle; its [API landing page](https://adobedocs.github.io/painter-python-api/api/)
currently says “Coming soon.” For exact installed-version documentation use
Painter **Help → Python API documentation**, or `api.inspect`. Installed references
live under `resources/python-doc` and `resources/javascript-doc` in the application
directory. Online release notes also confirm
[layerstack and baking API additions](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/release-notes/all-changes).

All rows map to `src/painter_mcp/adapter.py` and schemas in `catalog.py`. The live
suite is `scripts/validate_painter.py`, gated through `tests/licensed/test_painter.py`.
“Live” means the specified path was executed against Painter, not that every
resource, shader, platform, parameter combination or future version is certified.

| Workflow | Verified public API | Specialist implementation | Validation and limits |
|---|---|---|---|
| Project lifecycle | `project.create/open/close/save/save_as/save_as_copy`, `Settings`, `reload_mesh` callback | `project.*` | Live create, save, copy, close, reopen and mesh reload. Existing projects are never implicitly replaced; dirty close requires explicit discard. Reload completion uses `ReloadMeshStatus.SUCCESS`. |
| Project settings | `project.Settings`, `MeshReloadingSettings`, `Metadata`; JS `alg.mapexport.get/setProjectExportOptions`, `get/setProjectExportPreset` | `project.create`, `project.reload_mesh`, `project.metadata`, `settings.*` | Live creation normal-map format/resolution, metadata, export options. Full creation dataclasses can be constructed. Public APIs do not expose every existing-project configuration dialog setting. JS `alg.project.settings` is custom plugin data, not the project configuration dialog. |
| Texture sets and UV tiles | `all_texture_sets`, `TextureSet`, `Stack`, `Resolution`, `all_uv_tiles`, `UVTile.set_resolution`, `set_active_stack` | `texturesets.list/update` | Live texture-set rename preserving reference, resolution and active stack; two-UDIM project and per-tile resolution. Sets derive from imported mesh materials; no invented add/delete texture-set API. |
| Channels | `Stack.all_channels/add_channel/edit_channel/remove_channel` | `channels.*` | Live list/add/edit/remove. Enum names are Adobe names (`BaseColor`, `SpecularRoughness`, `BaseMetalness`), not guessed UI labels. |
| Layers and groups | `get_root_layer_nodes`, `get_node_by_uid`, `insert_fill/paint/group`, `instantiate`, `delete_node`, selection APIs; node name/visibility/blending/opacity | `layers.list/create/update/delete/select` | Live nested groups, fill/paint layers, instances, rename, blending/opacity, deletion, selection and pagination. No public general reorder/reparent or independent clone method was found in this version. Instancing is not duplication. |
| Masks and geometry masks | `LayerNode.add/remove_mask`, `enable_mask`, `get/set_geometry_mask`; `InsertPosition.inside_node`, `insert_smart_mask` | `masks.update`, `layers.create` | Live mask background/enabling/removal, mask fill, smart-mask insertion and native mesh/UV-tile geometry-mask dataclasses. A smart mask returns a list of effects. Color selection/comparison require a mask stack. |
| Effects | `insert_levels/filter/generator/anchor_point/color_selection/compare_mask_effect`, native parameter dataclasses | `layers.create/effect` | Live effect creation, level/channel and parameter inspection, generator/filter resource assignment. Typed parameter writes support nested dataclasses through `$type` or merged fields. |
| Materials and sources | `set_source`, `set_material_source`, `SourceSubstance` parameters/presets, `Color`, smart-material/mask export and creation | `sources.get/set`, `materials.save`, `layers.create` | Live uniform color, bitmap, procedural material, parameter round trip, smart material/mask export/import. `materials.save` without a path creates a **user-shelf** resource; a path exports only to that directory. Existing same-name files may be overwritten. |
| Projection and symmetry | `FillParamsEditorMixin`, `ProjectionMode`, native projection/symmetry dataclasses | `layers.projection` | Live UV and triplanar modes. Symmetry is available only for 3D projection; UV results report null. Native dataclasses remain available for all supported modes through API discovery. |
| Resources and shelves | `resource.search`, project/session/shelf import, `Shelves`, `replace_project_resources` | `resources.*` | Live search/pagination, project import, replacement, shelf enumeration/register/unregister. Shelf removal requires no open project. Resource searches enumerate native results before response pagination. No cloud-asset purchase/download automation. |
| Baking and mesh maps | `BakingParameters`, `bake_async`, selected bake, `StopSource.request_stop`, `BakingProcess*` events; mesh-map get/set | `baking.*`, `job.*`, `mesh_maps.*` | Live native property discovery/set, linking/unlinking, normal/AO bake, completion, mesh-map get/set. Cancellation is cooperative; a launch result is not bake completion. Baking changes UI mode. |
| Viewport and observation | `display.Camera.get_default_camera`, display resource methods, `ui.switch_to_mode`; public texture export | `viewport.*`, `painter_observe` | Live camera read/write, channel preview and Qt window capture. No dedicated public viewport-render capture/fence API was found. The explicitly labelled window fallback includes UI and may lag GPU facts. Texture previews return one tile with truncation, not a shaded 3D render. |
| Texture and mesh export | `list_project_textures`, `export_project_textures`, export preset enumeration, `export_mesh` | `export.*` | Live plan/export/preset list/mesh export. Texture configuration forwards Adobe's full JSON export schema. Cancellation/partial files are reported; filesystem writes have no rollback. |
| General scripting/API | public `substance_painter` modules, `js.evaluate` | `api.inspect/call`, `painter_script/session` | Live Python sessions, SDK, JavaScript and public API invocation. Full host privileges; no hard memory/time sandbox. Shader instance/parameter APIs and other JavaScript-only features remain accessible through Adobe's documented `alg` namespace. |

## Agent infrastructure and automated coverage

| Capability | Implementation | Painter-free tests |
|---|---|---|
| Compact tools and valid MCP schemas | `catalog.py`, official MCP SDK in `server.py` | `test_protocol.py`: real stdio initialize/list/call/errors/shutdown, no-Painter discovery |
| Batch references, preflight, selection and partial results | `engine.py`, `common.py` | `test_engine.py`: no edits on invalid batches, full-result references, partial failures, output/capture failure |
| Request recovery and transport controls | `broker.py`, `transport.py`, `client.py` | `test_broker.py`, `test_transport.py`: concurrent retries, conflicts, restart epoch, expiry tombstones, queued cancel, busy status, limits |
| Sessions, scoped guards and pagination | `state.py`, `engine.py` | `test_state.py`, `test_engine.py`: expiry, replacement, stale guards, byte/item pagination, persistent SDK/output limits |
| Startup/shutdown and binding regressions | `plugin.py`, `adapter.py` | `test_plugin.py`, `test_adapter_contracts.py`: timer/callback/listener cleanup, source properties, multi-effect masks, mono-channel and symmetry rules |
| Installation and distribution | `install.py`, scripts | `test_install.py`, `smoke_install.py`, `check_package.py`: fresh wheel installation, both clients, repair, conflicts, backups, uninstall, version/hash checks |

## Deliberate API boundaries

Freehand brush strokes, arbitrary layer movement/independent duplication, every
project dialog control, native framebuffer capture and complete evaluated-state
change tracking are not implemented as fictional APIs. No input-simulation fallback
is used for edits. Full scripting is provided for supported version-specific paths.
GUI-only operations require an operator or a separately authorized UI automation
tool; they are outside this server's reliable contract.
