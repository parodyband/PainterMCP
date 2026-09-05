"""Compact MCP surface; specialist schemas are requested only when needed."""

from .common import Fault


def obj(props=None, required=()):
    return {
        "type": "object",
        "properties": props or {},
        "required": list(required),
        "additionalProperties": False,
    }


def string(description="", enum=None):
    result = {"type": "string", "maxLength": 4096, "description": description}
    if enum is not None:
        result["enum"] = enum
    return result


def array(items=None, maximum=128):
    return {"type": "array", "items": items or {}, "maxItems": maximum}


BOOL = {"type": "boolean"}
ANY: dict = {}
JSON = {"type": "object"}
NUM = {"type": "number"}
REF = string("Opaque reference returned by Painter; invalidated by project replacement.")
PAGE = {
    "offset": {"type": "integer", "minimum": 0},
    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    "cursor": string("Snapshot token from previous page. Omit offset when using next_cursor."),
}
SCOPE = {
    "texture_set": string("Reference or exact current name; defaults to active."),
    "stack": string("Exact stack name. Defaults to active/default stack."),
}
OBSERVE = obj(
    {
        **SCOPE,
        "nodes": array(REF, 200),
        "image": string(enum=["none", "window", "texture"]),
        "channel": string("ChannelType enum name, e.g. BaseColor"),
        "width": {"type": "integer", "minimum": 128, "maximum": 1600},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    }
)
POSITION = obj(
    {
        **SCOPE,
        "node": REF,
        "where": string(enum=["above", "below", "inside"]),
        "stack_kind": string(enum=["Content", "Mask", "Substack"]),
    }
)
OPS: dict[str, dict] = {}


def op(name, description, props=None, required=(), api=(), mutates=False, undo="none"):
    OPS[name] = {
        "name": name,
        "description": description,
        "inputSchema": obj(props, required),
        "api": list(api),
        "mutates": mutates,
        "undo": undo,
    }


op(
    "project.info",
    "Current project, busy/dirty state, version and mesh path.",
    api=["project.is_open"],
)
op(
    "project.create",
    "Create from mesh. Refuses to replace any open project.",
    {"mesh": string(), "template": string(), "mesh_maps": array(string()), "settings": JSON},
    ["mesh"],
    ["project.create", "project.Settings"],
    True,
)
op(
    "project.open",
    "Open SPP. Refuses to replace any open project.",
    {"path": string()},
    ["path"],
    ["project.open"],
    True,
)
op(
    "project.save",
    "Save, save-as, or copy. Filesystem writes cannot be undone.",
    {"path": string(), "copy": BOOL},
    api=["project.save", "project.save_as", "project.save_as_copy"],
    mutates=True,
)
op(
    "project.close",
    "Close; dirty projects require discard:true. Prefer save first.",
    {"discard": BOOL},
    api=["project.close"],
    mutates=True,
)
op(
    "project.reload_mesh",
    "Start asynchronous mesh reload; query job.status for actual outcome.",
    {"path": string(), "settings": JSON},
    ["path"],
    ["project.reload_mesh", "project.MeshReloadingSettings"],
    True,
)
op(
    "project.metadata",
    "Read or write namespaced project metadata.",
    {"context": string(), "key": string(), "value": ANY},
    ["context"],
    ["project.Metadata"],
    True,
)
op(
    "settings.get",
    "Read saved export options/preset. Creation settings belong to project.create; project.metadata stores custom data.",
    api=["js.evaluate"],
)
op(
    "settings.set",
    "Set saved texture export options/preset using Adobe's JavaScript API.",
    {"export_options": JSON, "export_preset": string()},
    api=["js.evaluate"],
    mutates=True,
)
op(
    "texturesets.list",
    "Paginated texture sets, resolutions, stacks, UV tiles and mesh names.",
    PAGE,
    api=["textureset.all_texture_sets"],
)
op(
    "texturesets.update",
    "Rename, describe, resize, or activate a texture set or UV tile.",
    {
        **SCOPE,
        "name": string(),
        "description": string(),
        "resolution": array({"type": "integer"}, 2),
        "uv_tile": array({"type": "integer"}, 2),
        "activate": BOOL,
    },
    api=["textureset.TextureSet"],
    mutates=True,
)
op("channels.list", "Channels for one stack.", SCOPE, api=["textureset.Stack.all_channels"])
for action in ("add", "edit", "remove"):
    op(
        f"channels.{action}",
        f"{action.title()} a stack channel using ChannelType/ChannelFormat enum names.",
        {
            **SCOPE,
            "channel": string(),
            **({"format": string(), "label": string()} if action != "remove" else {}),
        },
        ["channel"] + (["format"] if action != "remove" else []),
        [f"textureset.Stack.{action}_channel"],
        True,
    )
op(
    "layers.list",
    "Paginated recursive layer/group/effect tree; stable references and selection facts.",
    {**SCOPE, **PAGE},
    api=["layerstack.get_root_layer_nodes"],
)
op(
    "layers.create",
    "Insert a layer, group, effect, smart material/mask or instance at an explicit position.",
    {
        "kind": string(
            enum=[
                "fill",
                "paint",
                "group",
                "levels",
                "filter",
                "generator",
                "anchor",
                "color_selection",
                "compare_mask",
                "smart_material",
                "smart_mask",
                "instance",
            ]
        ),
        "position": POSITION,
        "name": string(),
        "resource": string(),
        "source": REF,
    },
    ["kind"],
    ["layerstack.InsertPosition", "layerstack.insert_fill"],
    True,
    "layerstack",
)
op(
    "layers.update",
    "Set name, visibility, opacity, blending, active channels or group collapse.",
    {
        "node": REF,
        "name": string(),
        "visible": BOOL,
        "opacity": {**NUM, "minimum": 0, "maximum": 1},
        "channel": string(),
        "blending": string(),
        "active_channels": array(string()),
        "collapsed": BOOL,
    },
    ["node"],
    ["layerstack.get_node_by_uid"],
    True,
    "layerstack",
)
op(
    "layers.delete",
    "Delete a node (and its descendants).",
    {"node": REF},
    ["node"],
    ["layerstack.delete_node"],
    True,
    "layerstack",
)
op(
    "layers.select",
    "Select compatible layer/effect nodes and optionally content or mask.",
    {"nodes": array(REF), "selection_type": string()},
    ["nodes"],
    ["layerstack.set_selected_nodes"],
    True,
)
op(
    "masks.update",
    "Add/remove/enable a mask or configure its background and geometry filter.",
    {
        "node": REF,
        "action": string(enum=["add", "remove", "update"]),
        "background": string(enum=["Black", "White"]),
        "enabled": BOOL,
        "geometry_type": string(enum=["Mesh", "UVTile"]),
        "meshes": array(string()),
        "uv_tiles": array(array({"type": "integer"}, 2)),
    },
    ["node", "action"],
    ["layerstack.LayerNode.add_mask"],
    True,
    "layerstack",
)
op(
    "sources.get",
    "Inspect channel/material source and parameters.",
    {"node": REF, "channel": string(), "material": BOOL},
    ["node"],
    ["source.SourceEditorMixin.get_source"],
)
op(
    "sources.set",
    "Assign uniform RGB, bitmap, material, anchor, generator/filter resource, or reset source.",
    {
        "node": REF,
        "channel": string(),
        "material": BOOL,
        "resource": string(),
        "anchor": REF,
        "color": array(NUM, 3),
        "color_space": string(enum=["sRGB", "Working"]),
        "reset": BOOL,
        "parameters": JSON,
        "preset": string(),
    },
    ["node"],
    ["source.SourceEditorMixin.set_source"],
    True,
    "layerstack",
)
op(
    "layers.projection",
    "Read or edit projection mode/typed parameters and symmetry.",
    {
        "node": REF,
        "mode": string(),
        "parameters": JSON,
        "symmetry": BOOL,
        "symmetry_parameters": JSON,
    },
    ["node"],
    ["layerstack.FillParamsEditorMixin.get_projection_parameters"],
    True,
    "layerstack",
)
op(
    "layers.effect",
    "Read/write typed effect parameters (levels, comparison or color selection).",
    {"node": REF, "parameters": JSON, "channel": string()},
    ["node"],
    ["layerstack.LevelsEffectNode.get_parameters"],
    True,
    "layerstack",
)
op(
    "materials.save",
    "Create a smart material/mask in the user shelf; with path, export only to that directory (existing same-name file is overwritten).",
    {"node": REF, "name": string(), "kind": string(enum=["material", "mask"]), "path": string()},
    ["node", "name", "kind"],
    ["layerstack.create_smart_material"],
    True,
)
op(
    "resources.search",
    "Search local Painter resources using native query syntax. Snapshot pagination.",
    {"query": string(), **PAGE},
    ["query"],
    ["resource.search"],
)
op(
    "resources.import",
    "Import a file into project/session or a writable shelf.",
    {
        "path": string(),
        "usage": string(),
        "location": string(enum=["project", "session", "shelf"]),
        "shelf": string(),
        "name": string(),
    },
    ["path", "usage"],
    ["resource.import_project_resource"],
    True,
)
op(
    "resources.shelves",
    "List shelves, or add/remove/refresh a shelf registration.",
    {
        "action": string(enum=["list", "add", "remove", "refresh"]),
        "name": string(),
        "path": string(),
    },
    api=["resource.Shelves"],
    mutates=True,
)
op(
    "resources.update",
    "Replace a resource used by the layer stack.",
    {"old": string(), "new": string()},
    ["old", "new"],
    ["resource.replace_project_resources"],
    True,
)
op(
    "mesh_maps.get",
    "Inspect assigned baked mesh map resource.",
    {**SCOPE, "usage": string()},
    ["usage"],
    ["textureset.TextureSet.get_mesh_map_resource"],
)
op(
    "mesh_maps.set",
    "Assign a baked mesh map resource.",
    {**SCOPE, "usage": string(), "resource": string()},
    ["usage", "resource"],
    ["textureset.TextureSet.set_mesh_map_resource"],
    True,
)
op(
    "baking.get",
    "Inspect live baking parameter names, values, enum choices and enabled bakers.",
    {**SCOPE, "usage": string()},
    api=["baking.BakingParameters"],
)
op(
    "baking.set",
    "Set common/baker properties using names discovered with baking.get.",
    {
        **SCOPE,
        "usage": string(),
        "values": JSON,
        "enabled_bakers": array(string()),
        "enabled": BOOL,
        "uv_tiles": array(array({"type": "integer"}, 2)),
        "curvature": string(),
    },
    api=["baking.BakingParameters.set"],
    mutates=True,
)
op(
    "baking.link",
    "Link baking settings across texture sets, or unlink all for a usage.",
    {"texture_sets": array(string()), "reference": string(), "usage": string(), "unlink": BOOL},
    api=["baking.set_linked_group_common_parameters"],
    mutates=True,
)
op(
    "baking.start",
    "Launch baking; returns an app job, not completed textures. Poll job.status.",
    {**SCOPE, "selected": BOOL},
    api=["baking.bake_async", "event.BakingProcessEnded"],
    mutates=True,
)
op(
    "job.status",
    "Read completion/progress of an asynchronous baking or mesh reload job.",
    {"job_id": string()},
    ["job_id"],
)
op(
    "job.cancel",
    "Request cooperative cancellation of baking. No guarantee of immediate cancellation.",
    {"job_id": string()},
    ["job_id"],
    ["async_utils.StopSource.request_stop"],
    True,
)
op(
    "viewport.get",
    "Read the default 3D camera and display resources.",
    api=["display.Camera.get_default_camera"],
)
op(
    "viewport.set",
    "Set supported camera properties and display resources.",
    {
        "camera": JSON,
        "environment": string(),
        "color_lut": string(),
        "tone_mapping": string(),
        "mode": string(),
    },
    api=["display.Camera.get_default_camera"],
    mutates=True,
)
op(
    "export.presets",
    "List available export presets.",
    PAGE,
    api=["export.list_predefined_export_presets"],
)
op(
    "export.textures",
    "Plan or execute Adobe's full texture export JSON configuration. Reports export status/errors.",
    {"config": JSON, "dry_run": BOOL},
    ["config"],
    ["export.export_project_textures"],
    True,
)
op(
    "export.mesh",
    "Export mesh with native MeshExportOption enum.",
    {"path": string(), "option": string()},
    ["path", "option"],
    ["export.export_mesh"],
    True,
)
op(
    "api.inspect",
    "Discover public runtime API signatures, enums and documentation, including version-specific features.",
    {"path": string(), "target": REF, **PAGE},
    api=[],
)
op(
    "api.call",
    "General public API escape hatch. $type constructs enums/dataclasses; $handle resolves live objects.",
    {"path": string(), "target": REF, "args": array(), "kwargs": JSON, "get": BOOL, "set": ANY},
    ["path"],
    api=[],
    mutates=True,
)


def require_when(name, field, value, required):
    OPS[name]["inputSchema"].setdefault("allOf", []).append(
        {
            "if": {"properties": {field: {"const": value}}, "required": [field]},
            "then": {"required": required},
        }
    )


require_when("project.save", "copy", True, ["path"])
for kind in ("smart_material", "smart_mask"):
    require_when("layers.create", "kind", kind, ["resource"])
require_when("layers.create", "kind", "instance", ["source"])
require_when("resources.import", "location", "shelf", ["shelf"])
require_when("resources.shelves", "action", "add", ["name", "path"])
require_when("resources.shelves", "action", "remove", ["name"])
OPS["project.metadata"]["inputSchema"]["allOf"] = [
    {"if": {"required": ["value"]}, "then": {"required": ["key"]}}
]
OPS["baking.link"]["inputSchema"]["allOf"] = [
    {
        "if": {"properties": {"unlink": {"const": True}}, "required": ["unlink"]},
        "then": {},
        "else": {"required": ["texture_sets", "reference"]},
    }
]
for spec in OPS.values():
    properties = spec["inputSchema"]["properties"]
    for key, count in (("resolution", 2), ("uv_tile", 2), ("color", 3)):
        if key in properties:
            properties[key]["minItems"] = count
    if "uv_tiles" in properties:
        properties["uv_tiles"]["items"]["minItems"] = 2


STEP = obj(
    {
        "id": {**string(), "maxLength": 128},
        "op": string(),
        "args": JSON,
        "select": array(string("JSON pointers into the full step result; [] suppresses output.")),
    },
    ["id", "op"],
)
REF_OBSERVE = obj(
    {
        key: {
            "anyOf": [
                {**value, "items": {"anyOf": [value["items"], obj({"$ref": string()}, ["$ref"])]}}
                if value.get("type") == "array"
                else value,
                obj({"$ref": string()}, ["$ref"]),
            ]
        }
        for key, value in OBSERVE["properties"].items()
    }
)
RUN = obj(
    {
        "steps": {**array(STEP, 64), "minItems": 1},
        "if_observation": string(),
        "observe": REF_OBSERVE,
        "undo": string(enum=["individual", "layerstack"]),
        "request_id": string(),
        "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
    },
    ["steps"],
)
SCRIPT = obj(
    {
        "source": {"type": "string", "maxLength": 200000},
        "language": string(enum=["python", "javascript"]),
        "session_id": string(),
        "arguments": JSON,
        "if_observation": string(),
        "observe": OBSERVE,
        "request_id": string(),
        "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
    },
    ["source"],
)

CORE = {
    "painter_status": (
        "Connection, runtime capabilities and request limits. Works while Painter is busy.",
        obj(),
    ),
    "painter_describe": (
        "Discover specialist operation schemas or list domains. Runtime availability is authoritative.",
        obj({"names": array(string(), 20), "domain": string(), **PAGE}),
    ),
    "painter_observe": (
        "Combined project, texture set, channel, layer and selection facts with optional native MCP image.",
        OBSERVE,
    ),
    "painter_run": (
        "Batch specialist operations; earlier-result $refs, selected outputs, guards and post-observation. Not atomic.",
        RUN,
    ),
    "painter_script": (
        "Full-privilege persistent Python or Adobe JavaScript escape hatch. Assign result; use arguments and painter SDK.",
        SCRIPT,
    ),
    "painter_session": (
        "Open/status/reset/close a bounded-lifetime Python namespace.",
        obj(
            {"action": string(enum=["open", "status", "reset", "close"]), "session_id": string()},
            ["action"],
        ),
    ),
    "painter_request": (
        "Recover a request by ID. Cancel only queued work. Never rerun an unknown edit blindly.",
        obj({"request_id": string(), "action": string(enum=["status", "cancel"])}, ["request_id"]),
    ),
    "painter_changes": (
        "Compare a scoped observation cursor against current covered facts. Snapshot comparison, not a complete event history.",
        obj({"cursor": string()}, ["cursor"]),
    ),
}


def tools_list():
    return [
        {
            "name": name,
            "description": desc,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": name
                in {"painter_status", "painter_describe", "painter_observe", "painter_changes"},
                "openWorldHint": False,
            },
        }
        for name, (desc, schema) in CORE.items()
    ]


def operation(name):
    try:
        return OPS[name]
    except KeyError as exc:
        raise Fault(
            "UNKNOWN_OPERATION", f"Discover the schema for {name} with painter_describe"
        ) from exc
