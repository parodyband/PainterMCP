"""Adobe public API adapter. Loaded only inside Painter; never on hosted CI."""

from __future__ import annotations

import base64
import dataclasses
import enum
import importlib
import inspect
import json
import math
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from .catalog import OPS
from .common import MAX_IMAGE, Fault, dumps


def enum_name(value):
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value)


class PainterAdapter:
    def __init__(self, state, sp=None):
        if sp is None:
            sp = importlib.import_module("substance_painter")
        self.sp = sp
        self.state = state
        self.thread_id = threading.get_ident()
        self.events = []
        self.jobs: OrderedDict[str, dict] = OrderedDict()
        self.bake_job = None
        self.external_bake = False
        for name in ("ProjectCreated", "ProjectOpened", "ProjectClosed"):
            self.connect(name, self._project_changed)
        self.connect("BakingProcessAboutToStart", self._bake_started)
        self.connect("BakingProcessProgress", self._bake_progress)
        self.connect("BakingProcessEnded", self._bake_ended)

    def assert_thread(self):
        if threading.get_ident() != self.thread_id:
            raise Fault("WRONG_THREAD", "Painter APIs must run on the application thread")

    def connect(self, name, callback):
        event = getattr(self.sp.event, name, None)
        if event is not None:
            self.sp.event.DISPATCHER.connect_strong(event, callback)
            self.events.append((event, callback))

    def close(self):
        self.assert_thread()
        for event, callback in self.events:
            self.sp.event.DISPATCHER.disconnect(event, callback)
        self.events.clear()
        self.state.reset_project()

    def _project_changed(self, event):
        self.state.reset_project()
        for job in self.jobs.values():
            if job["state"] == "running":
                job.update(state="unknown", reason="project_replaced")
        self.bake_job = None

    def _bake_started(self, event):
        if self.bake_job:
            self.jobs[self.bake_job]["stop"] = event.stop_source
        else:
            self.external_bake = True

    def _bake_progress(self, event):
        if self.bake_job:
            self.jobs[self.bake_job]["progress"] = event.progress

    def _bake_ended(self, event):
        if self.bake_job:
            status = enum_name(event.status)
            self.jobs[self.bake_job].update(
                state="completed" if status == "Success" else "failed", status=status, progress=1.0
            )
            self.bake_job = None
        self.external_bake = False

    def busy(self):
        self.assert_thread()
        return self.sp.project.is_busy()

    def public(self, path, target=None):
        value = self.object(target) if target else self.sp
        if path.startswith("substance_painter."):
            path = path[len("substance_painter.") :]
        if not path:
            return value
        for part in path.split("."):
            if not part.isidentifier() or part.startswith("_"):
                raise Fault(
                    "INVALID_API_PATH",
                    "Use public API names; arbitrary Python belongs in painter_script",
                )
            try:
                value = getattr(value, part)
            except AttributeError as exc:
                raise Fault("UNSUPPORTED_API", f"Runtime does not expose {path}") from exc
        return value

    def capabilities(self):
        self.assert_thread()
        available = {}
        for name, spec in OPS.items():
            missing = []
            for path in spec["api"]:
                try:
                    self.public(path)
                except Fault:
                    missing.append(path)
            available[name] = {"available": not missing, "missing": missing}
        return {
            "painter_version": self.sp.application.version(),
            "operations": available,
            "python_version": __import__("sys").version.split()[0],
            "qt": "PySide6",
            "capture": {"texture": "public texture export", "window": "Qt window capture fallback"},
            "undo": "ScopedModification for layerstack operations only; no automatic rollback",
            "script": "full host privileges; Python and JavaScript",
            "api_inventory": "api.inspect",
        }

    def decode(self, value):
        if isinstance(value, dict):
            if set(value) == {"$handle"}:
                return self.object(value["$handle"])
            if set(value) == {"$enum"}:
                return self.public(value["$enum"])
            if "$type" in value:
                cls = self.public(value["$type"])
                return cls(
                    *self.decode(value.get("args", [])), **self.decode(value.get("kwargs", {}))
                )
            return {k: self.decode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.decode(v) for v in value]
        return value

    def encode(self, value, depth=0):
        if depth > 12:
            return {"truncated": True, "reason": "depth_limit"}
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, enum.Enum) or hasattr(type(value), "__members__"):
            return enum_name(value)
        if isinstance(value, self.sp.layerstack.Node):
            return self.node_info(value, details=False)
        if isinstance(value, self.sp.textureset.TextureSet):
            return self.ts_info(value)
        if isinstance(value, self.sp.textureset.Stack):
            return {
                "ref": self.state.ref("stack", value.stack_id),
                "name": value.name(),
                "texture_set": self.state.ref("textureset", value.material().material_id),
            }
        if isinstance(value, self.sp.resource.ResourceID):
            return value.url()
        if isinstance(value, self.sp.resource.Resource):
            return {
                "url": value.identifier().url(),
                "name": value.gui_name(),
                "type": enum_name(value.type()),
                "usages": [enum_name(x) for x in value.usages()],
            }
        if isinstance(value, dict):
            return {str(k): self.encode(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self.encode(v, depth + 1) for v in value]
        if dataclasses.is_dataclass(value):
            return {
                f.name: self.encode(getattr(value, f.name), depth + 1)
                for f in dataclasses.fields(value)
            }
        return {"$handle": self.state.retain(value), "type": type(value).__name__}

    def object(self, token):
        if token.startswith("node:"):
            try:
                return self.sp.layerstack.get_node_by_uid(int(self.state.identity(token, "node")))
            except ValueError as exc:
                raise Fault("STALE_REFERENCE", "Layer/effect was deleted; observe again") from exc
        if token.startswith("textureset:"):
            identity = int(self.state.identity(token, "textureset"))
            for ts in self.sp.textureset.all_texture_sets():
                if ts.material_id == identity:
                    return ts
            raise Fault("STALE_REFERENCE", "Texture set no longer exists")
        if token.startswith("stack:"):
            identity = int(self.state.identity(token, "stack"))
            for ts in self.sp.textureset.all_texture_sets():
                for stack in ts.all_stacks():
                    if stack.stack_id == identity:
                        return stack
            raise Fault("STALE_REFERENCE", "Stack no longer exists")
        return self.state.dereference(token)

    def stack(self, args):
        if not args.get("texture_set") and "stack" not in args:
            return self.sp.textureset.get_active_stack()
        ts = self.texture_set(args)
        return ts.get_stack(args.get("stack", ""))

    def texture_set(self, args):
        name = args.get("texture_set")
        if not name:
            return self.sp.textureset.get_active_stack().material()
        if name.startswith("textureset:"):
            return self.object(name)
        return self.sp.textureset.TextureSet.from_name(name)

    def rid(self, value):
        return self.sp.resource.ResourceID.from_url(value)

    def channel(self, args):
        return (
            self.public("textureset.ChannelType." + args["channel"])
            if args.get("channel")
            else None
        )

    def project_info(self):
        opened = self.sp.project.is_open()
        info = {"open": opened, "busy": self.sp.project.is_busy(), "epoch": self.state.epoch}
        if opened:
            info.update(
                name=self.sp.project.name(),
                path=self.sp.project.file_path(),
                needs_saving=self.sp.project.needs_saving(),
                mesh=self.sp.project.last_imported_mesh_path(),
                edition_state=self.sp.project.is_in_edition_state(),
            )
        return info

    def ts_info(self, ts):
        return {
            "ref": self.state.ref("textureset", ts.material_id),
            "name": ts.name(),
            "resolution": self.encode(ts.get_resolution()),
            "stacks": [
                {"ref": self.state.ref("stack", s.stack_id), "name": s.name()}
                for s in ts.all_stacks()
            ],
            "uv_tiles": [{"u": t.u, "v": t.v} for t in ts.all_uv_tiles()]
            if ts.has_uv_tiles()
            else [],
            "meshes": ts.all_mesh_names(),
        }

    def channels_info(self, stack):
        return [
            {
                "type": enum_name(kind),
                "format": enum_name(channel.format()),
                "label": channel.label(),
            }
            for kind, channel in stack.all_channels().items()
        ]

    def node_info(self, node, details=True):
        info = {
            "ref": self.state.ref("node", node.uid()),
            "name": node.get_name(),
            "type": enum_name(node.get_type()),
            "visible": node.is_visible(),
        }
        if not details:
            return info
        if node.has_blending():
            channels = [None] if node.is_in_mask_stack() else list(node.get_stack().all_channels())
            info["blending"] = {
                enum_name(c) if c else "mask": {
                    "opacity": node.get_opacity(c),
                    "mode": enum_name(node.get_blending_mode(c)),
                }
                for c in channels
            }
        if isinstance(node, self.sp.layerstack.LayerNode):
            info["mask"] = {"present": node.has_mask()}
            if node.has_mask():
                info["mask"].update(
                    enabled=node.is_mask_enabled(), background=enum_name(node.get_mask_background())
                )
            try:
                active = self.sp.textureset.get_active_stack()
            except RuntimeError:
                active = None
            info["selection_type"] = (
                enum_name(self.sp.layerstack.get_selection_type(node))
                if active is not None and active.stack_id == node.get_stack().stack_id
                else None
            )
        if not node.is_in_mask_stack() and hasattr(node, "active_channels"):
            info["active_channels"] = sorted(enum_name(c) for c in node.active_channels)
        return info

    def layer_tree(self, stack, maximum=10000):
        result: list[dict] = []
        todo = [
            (n, None, "layer") for n in reversed(self.sp.layerstack.get_root_layer_nodes(stack))
        ]
        while todo and len(result) < maximum:
            node, parent, location = todo.pop()
            info = self.node_info(node)
            info.update(parent=parent, location=location)
            result.append(info)
            children = []
            if isinstance(node, self.sp.layerstack.GroupLayerNode):
                children += [(n, info["ref"], "substack") for n in node.sub_layers()]
            if isinstance(node, self.sp.layerstack.LayerNode):
                children += [(n, info["ref"], "content") for n in node.content_effects()]
                if node.has_mask():
                    children += [(n, info["ref"], "mask") for n in node.mask_effects()]
            todo.extend(reversed(children))
        return result, bool(todo)

    def facts(self, args):
        self.assert_thread()
        result = {"project": self.project_info()}
        if not result["project"]["open"]:
            return result
        try:
            active_stack = self.sp.textureset.get_active_stack()
        except RuntimeError:
            active_stack = None
        if active_stack is None and not args.get("texture_set"):
            result.update(
                active_stack=None,
                scope_required=True,
                hint="No active painting stack in this UI mode; pass texture_set explicitly",
                texture_sets=[self.ts_info(ts) for ts in self.sp.textureset.all_texture_sets()][
                    :20
                ],
            )
            return result
        stack = self.stack(args)
        ts = stack.material()
        if "nodes" in args:
            nodes = [self.object(token) for token in args["nodes"]]
            if any(node.get_stack().stack_id != stack.stack_id for node in nodes):
                raise Fault(
                    "SCOPE_MISMATCH",
                    "Observed nodes must belong to the requested texture set/stack",
                )
            layers = [self.node_info(node) for node in nodes[: args.get("limit", 60)]]
            truncated = len(nodes) > len(layers)
        else:
            layers, truncated = self.layer_tree(stack, args.get("limit", 60))
        result.update(
            texture_set=self.ts_info(ts),
            stack=self.encode(stack),
            active_stack=self.encode(active_stack),
            channels=self.channels_info(stack),
            layers=layers,
            selection=[
                self.state.ref("node", n.uid())
                for n in self.sp.layerstack.get_selected_nodes(stack)
            ]
            if active_stack is not None and active_stack.stack_id == stack.stack_id
            else None,
            selection_available=active_stack is not None
            and active_stack.stack_id == stack.stack_id,
            truncated=truncated,
        )
        return result

    def position(self, args):
        pos = self.sp.layerstack.InsertPosition
        if args.get("node"):
            node = self.object(args["node"])
            where = args.get("where", "above")
            if where == "inside":
                return pos.inside_node(
                    node, self.public("layerstack.NodeStack." + args.get("stack_kind", "Substack"))
                )
            return getattr(pos, where + "_node")(node)
        return pos.from_textureset_stack(self.stack(args))

    def source_info(self, source):
        info = {"type": type(source).__name__, "$handle": self.state.retain(source)}
        for name in (
            "resource_id",
            "get_parameters",
            "get_color",
            "image_inputs",
            "image_outputs",
            "get_preset_list",
        ):
            if hasattr(source, name):
                value = getattr(source, name)
                info[name.removeprefix("get_")] = self.encode(value() if callable(value) else value)
        return info

    def source(self, node, args):
        if args.get("material"):
            return node.get_material_source()
        if isinstance(
            node, (self.sp.layerstack.FilterEffectNode, self.sp.layerstack.GeneratorEffectNode)
        ):
            return node.get_source()
        return node.get_source(self.channel(args))

    def parameters(self, current, updates):
        """Merge JSON onto native dataclasses without replacing nested native enum types."""
        if "$type" in updates:
            return self.decode(updates)
        if not dataclasses.is_dataclass(current):
            raise Fault("INVALID_ARGUMENT", "Use a $type constructor for non-dataclass parameters")
        fields = {f.name for f in dataclasses.fields(current)}
        if set(updates) - fields:
            raise Fault("INVALID_ARGUMENT", f"Unknown fields: {sorted(set(updates) - fields)}")
        values = {}
        for key, value in updates.items():
            old = getattr(current, key)
            if dataclasses.is_dataclass(old) and isinstance(value, dict) and "$type" not in value:
                value = self.parameters(old, value)
            elif isinstance(value, str) and isinstance(old, enum.Enum):
                value = type(old)[value]
            else:
                value = self.decode(value)
            values[key] = value
        return dataclasses.replace(cast(Any, current), **values)

    def new_job(self, kind):
        while len(self.jobs) >= 64:
            expired = next((k for k, v in self.jobs.items() if v["state"] != "running"), None)
            if expired is None:
                raise Fault("JOB_LIMIT", "Too many running application jobs")
            del self.jobs[expired]
        token = "job:" + uuid.uuid4().hex
        self.jobs[token] = {"job_id": token, "kind": kind, "state": "running", "progress": 0.0}
        return token

    def invoke(self, name, a):
        self.assert_thread()
        sp = self.sp
        if name == "project.info":
            return self.project_info()
        if name in ("project.open", "project.create"):
            if sp.project.is_open():
                raise Fault(
                    "PROJECT_ALREADY_OPEN",
                    "Save/close the current project explicitly before replacing it",
                )
            if name.endswith("open"):
                sp.project.open(a["path"])
            else:
                settings = self.parameters(sp.project.Settings(), a.get("settings", {}))
                sp.project.create(a["mesh"], a.get("mesh_maps"), a.get("template"), settings)
            return self.project_info()
        if name == "project.close":
            if sp.project.is_open() and sp.project.needs_saving() and not a.get("discard", False):
                raise Fault("UNSAVED_PROJECT", "Save first or explicitly set discard:true")
            sp.project.close()
            return self.project_info()
        if name == "project.save":
            if a.get("copy") and not a.get("path"):
                raise Fault("INVALID_ARGUMENT", "Saving a copy requires path")
            if a.get("path"):
                (sp.project.save_as_copy if a.get("copy") else sp.project.save_as)(a["path"])
            else:
                sp.project.save()
            return {"path": a.get("path", sp.project.file_path()), "copy": a.get("copy", False)}
        if name == "project.reload_mesh":
            token = self.new_job("reload_mesh")

            def done(status):
                self.jobs[token].update(
                    state="completed" if enum_name(status) == "SUCCESS" else "failed",
                    status=enum_name(status),
                )
                self.state.reset_project()

            try:
                sp.project.reload_mesh(
                    a["path"],
                    self.parameters(sp.project.MeshReloadingSettings(), a.get("settings", {})),
                    done,
                )
            except Exception:
                self.jobs[token].update(state="failed", status="launch_error")
                raise
            return {"job_id": token, "state": self.jobs[token]["state"]}
        if name == "project.metadata":
            metadata = sp.project.Metadata(a["context"])
            if "value" in a:
                if "key" not in a:
                    raise Fault("INVALID_ARGUMENT", "Writing metadata requires key")
                metadata.set(a["key"], a["value"])
            return self.encode(metadata.get(a["key"]) if "key" in a else metadata.list())
        if name.startswith("settings."):
            if name.endswith("set"):
                if "export_options" in a:
                    sp.js.evaluate(
                        "alg.mapexport.setProjectExportOptions(" + dumps(a["export_options"]) + ")"
                    )
                if "export_preset" in a:
                    sp.js.evaluate(
                        "alg.mapexport.setProjectExportPreset(" + dumps(a["export_preset"]) + ")"
                    )
            return self.js_json(
                "({export_options:alg.mapexport.getProjectExportOptions(),export_preset:alg.mapexport.getProjectExportPreset()})"
            )
        if name == "texturesets.list":
            return self.state.paginate(
                name, lambda: [self.ts_info(t) for t in sp.textureset.all_texture_sets()], a
            )
        if name == "texturesets.update":
            ts = self.texture_set(a)
            target = ts.uv_tile(*a["uv_tile"]) if "uv_tile" in a else ts
            if "resolution" in a:
                if len(a["resolution"]) != 2:
                    raise Fault("INVALID_ARGUMENT", "resolution requires width and height")
                target.set_resolution(sp.textureset.Resolution(*a["resolution"]))
            for attr in ("name", "description"):
                if attr in a:
                    setattr(target, attr, a[attr])
            if a.get("activate"):
                sp.textureset.set_active_stack(ts.get_stack(a.get("stack", "")))
            return self.ts_info(ts)
        if name.startswith("channels."):
            stack = self.stack(a)
            action = name.split(".")[1]
            if action != "list":
                args = [self.channel(a)]
                if action != "remove":
                    args += [self.public("textureset.ChannelFormat." + a["format"])]
                    if "label" in a:
                        args.append(a["label"])
                getattr(stack, action + "_channel")(*args)
            return self.channels_info(stack)
        if name == "layers.list":
            stack = self.stack(a)

            def produce():
                tree, truncated = self.layer_tree(stack)
                if truncated:
                    raise Fault(
                        "QUERY_TOO_LARGE",
                        "Layer tree exceeds 10000 nodes; inspect a group via api.call",
                    )
                return tree

            return self.state.paginate(name + str(stack.stack_id), produce, a)
        if name == "layers.create":
            kind = a["kind"]
            position = self.position(a.get("position", {}))
            names = {
                "levels": "levels_effect",
                "filter": "filter_effect",
                "generator": "generator_effect",
                "anchor": "anchor_point_effect",
                "color_selection": "color_selection_effect",
                "compare_mask": "compare_mask_effect",
            }
            extra = []
            if kind in ("smart_material", "smart_mask"):
                extra = [self.rid(a["resource"])]
            if kind in ("filter", "generator") and "resource" in a:
                extra = [self.rid(a["resource"])]
            if kind == "anchor":
                extra = [a.get("name", "Agent anchor")]
            if kind == "instance":
                node = sp.layerstack.instantiate(position, self.object(a["source"]))
            else:
                node = getattr(sp.layerstack, "insert_" + names.get(kind, kind))(position, *extra)
            if isinstance(node, list):
                if "name" in a:
                    for index, effect in enumerate(node):
                        effect.set_name(f"{a['name']} {index + 1}")
                return {"nodes": [self.node_info(effect) for effect in node], "kind": kind}
            if "name" in a:
                node.set_name(a["name"])
            return self.node_info(node)
        if name == "layers.update":
            node = self.object(a["node"])
            for field, method in (
                ("name", "set_name"),
                ("visible", "set_visible"),
                ("collapsed", "set_collapsed"),
            ):
                if field in a:
                    getattr(node, method)(a[field])
            if "active_channels" in a:
                node.active_channels = {
                    self.public("textureset.ChannelType." + c) for c in a["active_channels"]
                }
            if "opacity" in a:
                node.set_opacity(a["opacity"], self.channel(a))
            if "blending" in a:
                node.set_blending_mode(
                    self.public("layerstack.BlendingMode." + a["blending"]), self.channel(a)
                )
            return self.node_info(node)
        if name == "layers.delete":
            sp.layerstack.delete_node(self.object(a["node"]))
            return {"deleted": a["node"]}
        if name == "layers.select":
            nodes = [self.object(n) for n in a["nodes"]]
            sp.layerstack.set_selected_nodes(nodes)
            if "selection_type" in a:
                for node in nodes:
                    sp.layerstack.set_selection_type(
                        node, self.public("layerstack.SelectionType." + a["selection_type"])
                    )
            return {"selected": a["nodes"]}
        if name == "masks.update":
            node = self.object(a["node"])
            background = self.public("layerstack.MaskBackground." + a.get("background", "Black"))
            if a["action"] == "add":
                node.add_mask(background)
            elif a["action"] == "remove":
                node.remove_mask()
            elif "background" in a:
                node.set_mask_background(background)
            if "enabled" in a:
                node.enable_mask(a["enabled"])
            if any(k in a for k in ("geometry_type", "meshes", "uv_tiles")):
                if "meshes" in a and "uv_tiles" in a:
                    raise Fault("INVALID_ARGUMENT", "Choose mesh or UV-tile geometry filtering")
                ts = node.get_texture_set()
                kind = a.get("geometry_type", "UVTile" if "uv_tiles" in a else "Mesh")
                if kind == "UVTile":
                    tiles = (
                        [ts.uv_tile(*uv) for uv in a["uv_tiles"]]
                        if "uv_tiles" in a
                        else ts.all_uv_tiles()
                    )
                    node.set_geometry_mask(sp.layerstack.GeometryMaskUVTilesParams(True, tiles))
                else:
                    node.set_geometry_mask(
                        sp.layerstack.GeometryMaskMeshParams(
                            True, a.get("meshes", ts.all_mesh_names())
                        )
                    )
            return self.node_info(node)
        if name in ("sources.get", "sources.set"):
            node = self.object(a["node"])
            channel = self.channel(a)
            effect = isinstance(
                node, (sp.layerstack.FilterEffectNode, sp.layerstack.GeneratorEffectNode)
            )
            if name.endswith("set"):
                if sum(key in a for key in ("resource", "anchor", "color")) > 1:
                    raise Fault("INVALID_ARGUMENT", "Choose one of resource, anchor, color")
                if a.get("reset"):
                    if a.get("material"):
                        node.reset_material_source()
                    elif effect:
                        node.remove_source()
                    else:
                        node.reset_source(channel)
                    return {"reset": True}
                source_value = None
                if "resource" in a:
                    source_value = self.rid(a["resource"])
                elif "anchor" in a:
                    source_value = self.object(a["anchor"])
                elif "color" in a:
                    if len(a["color"]) != 3:
                        raise Fault("INVALID_ARGUMENT", "color requires three RGB values")
                    source_value = sp.colormanagement.Color(*a["color"])
                    if a.get("color_space", "sRGB") == "sRGB":
                        source_value.sRGB = tuple(a["color"])
                    else:
                        source_value.working = tuple(a["color"])
                if source_value is not None:
                    if a.get("material"):
                        node.set_material_source(source_value)
                    elif effect:
                        node.set_source(source_value)
                    else:
                        node.set_source(channel, source_value)
                source = self.source(node, a)
                if "parameters" in a:
                    source.set_parameters(self.decode(a["parameters"]))
                if "preset" in a:
                    source.apply_preset(a["preset"])
            return self.source_info(self.source(node, a))
        if name in ("layers.projection", "layers.effect"):
            node = self.object(a["node"])
            if name.endswith("projection"):
                if "mode" in a:
                    node.set_projection_mode(self.public("layerstack.ProjectionMode." + a["mode"]))
                if "parameters" in a:
                    node.set_projection_parameters(
                        self.parameters(node.get_projection_parameters(), a["parameters"])
                    )
                if "symmetry" in a:
                    node.set_symmetry_enabled(a["symmetry"])
                if "symmetry_parameters" in a:
                    node.set_symmetry_parameters(
                        self.parameters(node.get_symmetry_parameters(), a["symmetry_parameters"])
                    )
                return {
                    "mode": enum_name(node.get_projection_mode()),
                    "parameters": self.encode(node.get_projection_parameters()),
                    "symmetry": node.is_symmetry_enabled()
                    if sp.layerstack.is_3d_projection_mode(node.get_projection_mode())
                    else None,
                }
            if "channel" in a:
                node.affected_channel = channel = self.channel(a)
            if "parameters" in a:
                node.set_parameters(self.parameters(node.get_parameters(), a["parameters"]))
            return self.encode(node.get_parameters())
        if name == "materials.save":
            node = self.object(a["node"])
            kind = "smart_" + a["kind"]
            if "path" in a:
                getattr(sp.layerstack, "export_as_" + kind)(node, a["name"], a["path"])
                return {"path": a["path"]}
            return self.encode(getattr(sp.layerstack, "create_" + kind)(node, a["name"]))
        if name == "resources.search":
            return self.state.paginate(
                name + a["query"],
                lambda: [self.encode(r) for r in sp.resource.search(a["query"])],
                a,
            )
        if name == "resources.import":
            location = a.get("location", "project")
            args = [a["path"], self.public("resource.Usage." + a["usage"])]
            kwargs = {"name": a["name"]} if "name" in a else {}
            if location == "shelf":
                shelf = next(
                    (s for s in sp.resource.Shelves.all() if s.name() == a.get("shelf")), None
                )
                if not shelf:
                    raise Fault("INVALID_ARGUMENT", "Unknown shelf; inspect resources.shelves")
                return self.encode(shelf.import_resource(*args, **kwargs))
            return self.encode(
                getattr(sp.resource, "import_" + location + "_resource")(*args, **kwargs)
            )
        if name == "resources.shelves":
            action = a.get("action", "list")
            if action == "add":
                sp.resource.Shelves.add(a["name"], a["path"])
            if action == "remove":
                sp.resource.Shelves.remove(a["name"])
            if action == "refresh":
                sp.resource.Shelves.refresh_all()
            return [
                {
                    "name": s.name(),
                    "path": s.path(),
                    "writable": s.can_import_resources(),
                    "crawling": s.is_crawling(),
                }
                for s in sp.resource.Shelves.all()
            ]
        if name == "resources.update":
            result = sp.resource.replace_project_resources({self.rid(a["old"]): self.rid(a["new"])})
            if result.status != sp.resource.UpdateProjectStatus.SUCCESS:
                raise Fault(
                    "RESOURCE_UPDATE_FAILED",
                    "Painter rejected resource replacement",
                    result=self.encode(result),
                )
            return self.encode(result)
        if name.startswith("mesh_maps."):
            ts = self.texture_set(a)
            usage = self.public("textureset.MeshMapUsage." + a["usage"])
            if name.endswith("set"):
                ts.set_mesh_map_resource(usage, self.rid(a["resource"]) if a["resource"] else None)
            return self.encode(ts.get_mesh_map_resource(usage))
        if name in ("baking.get", "baking.set"):
            params = sp.baking.BakingParameters.from_texture_set(self.texture_set(a))
            properties = (
                params.baker(self.public("textureset.MeshMapUsage." + a["usage"]))
                if "usage" in a
                else params.common()
            )
            if name.endswith("set"):
                values = a.get("values", {})
                if set(values) - properties.keys():
                    raise Fault(
                        "INVALID_ARGUMENT", "Unknown baking property; inspect baking.get first"
                    )
                sp.baking.BakingParameters.set(
                    {properties[k]: self.decode(v) for k, v in values.items()}
                )
                if "enabled_bakers" in a:
                    params.set_enabled_bakers(
                        [self.public("textureset.MeshMapUsage." + x) for x in a["enabled_bakers"]]
                    )
                if "enabled" in a:
                    params.set_textureset_enabled(a["enabled"])
                if "curvature" in a:
                    params.set_curvature_method(
                        self.public("baking.CurvatureMethod." + a["curvature"])
                    )
                if "uv_tiles" in a:
                    params.set_enabled_uv_tiles(
                        [params.texture_set().uv_tile(*uv) for uv in a["uv_tiles"]]
                    )
            return {
                "properties": {
                    k: {
                        "value": self.encode(p.value()),
                        "label": p.label(),
                        "enums": p.enum_values(),
                    }
                    for k, p in properties.items()
                },
                "enabled_bakers": [enum_name(x) for x in params.get_enabled_bakers()],
                "enabled": params.is_textureset_enabled(),
            }
        if name == "baking.link":
            common = "usage" not in a
            usage = [] if common else [self.public("textureset.MeshMapUsage." + a["usage"])]
            if a.get("unlink"):
                (sp.baking.unlink_all_common_parameters if common else sp.baking.unlink_all)(*usage)
            else:
                group = [self.texture_set({"texture_set": n}) for n in a["texture_sets"]]
                reference = self.texture_set({"texture_set": a["reference"]})
                (
                    sp.baking.set_linked_group_common_parameters
                    if common
                    else sp.baking.set_linked_group
                )(group, reference, *usage)
            return {"linked": not a.get("unlink", False)}
        if name == "baking.start":
            if self.bake_job or self.external_bake:
                raise Fault("BAKING_ACTIVE", "Wait for the current bake to finish")
            token = self.new_job("baking")
            self.bake_job = token
            try:
                stop = (
                    sp.baking.bake_selected_textures_async()
                    if a.get("selected")
                    else sp.baking.bake_async(self.texture_set(a))
                )
                self.jobs[token]["stop"] = stop
            except Exception:
                self.jobs[token].update(state="failed", status="launch_error")
                self.bake_job = None
                raise
            return {"job_id": token, "state": self.jobs[token]["state"], "launch_only": True}
        if name in ("job.status", "job.cancel"):
            job = self.jobs.get(a["job_id"])
            if not job:
                raise Fault("JOB_EXPIRED", "Job unknown or evicted; inspect application state")
            if name.endswith("cancel"):
                if "stop" not in job:
                    raise Fault(
                        "CANCELLATION_UNSUPPORTED", "This operation has no public cancellation API"
                    )
                job["stop_requested"] = (
                    bool(job["stop"].request_stop()) if job["state"] == "running" else False
                )
            return {k: v for k, v in job.items() if k != "stop"}
        if name.startswith("viewport."):
            if name.endswith("set") and "mode" in a:
                sp.ui.switch_to_mode(self.public("ui.UIMode." + a["mode"]))
            camera = sp.display.Camera.get_default_camera()
            attrs = (
                "position",
                "rotation",
                "field_of_view",
                "focal_length",
                "focus_distance",
                "aperture",
                "orthographic_height",
                "projection_type",
            )
            if name.endswith("set"):
                values = a.get("camera", {})
                if set(values) - set(attrs):
                    raise Fault("INVALID_ARGUMENT", "Unsupported camera property")
                for key, value in values.items():
                    if key == "projection_type":
                        value = self.public("display.CameraProjectionType." + value)
                    setattr(camera, key, value)
                for key, method in (
                    ("environment", "set_environment_resource"),
                    ("color_lut", "set_color_lut_resource"),
                ):
                    if key in a:
                        getattr(sp.display, method)(self.rid(a[key]))
                if "tone_mapping" in a:
                    sp.display.set_tone_mapping(
                        self.public("display.ToneMappingFunction." + a["tone_mapping"])
                    )
                if "mode" in a:
                    sp.ui.switch_to_mode(self.public("ui.UIMode." + a["mode"]))
            return {
                "camera": {key: self.encode(getattr(camera, key)) for key in attrs},
                "environment": self.encode(sp.display.get_environment_resource()),
                "color_lut": self.encode(sp.display.get_color_lut_resource()),
            }
        if name == "export.presets":

            def presets():
                result = []
                for preset in (
                    sp.export.list_predefined_export_presets()
                    + sp.export.list_resource_export_presets()
                ):
                    result.append(self.encode(preset))
                return result

            return self.state.paginate(name, presets, a)
        if name == "export.textures":
            if a.get("dry_run"):
                return {"planned": self.encode(sp.export.list_project_textures(a["config"]))}
            result = sp.export.export_project_textures(a["config"])
            info = self.encode(result)
            if result.status != sp.export.ExportStatus.Success:
                raise Fault(
                    "EXPORT_INCOMPLETE",
                    "Texture export did not finish successfully",
                    export=info,
                    partial_files_possible=True,
                )
            return info
        if name == "export.mesh":
            return self.encode(
                sp.export.export_mesh(
                    a["path"], self.public("export.MeshExportOption." + a["option"])
                )
            )
        if name == "api.inspect":
            target = self.public(a.get("path", ""), a.get("target"))

            def members():
                result = []
                for key in sorted(x for x in dir(target) if not x.startswith("_")):
                    try:
                        value = inspect.getattr_static(target, key)
                        signature = str(inspect.signature(value)) if callable(value) else None
                    except (ValueError, TypeError):
                        signature = "signature unavailable"
                    result.append(
                        {"name": key, "signature": signature, "kind": type(value).__name__}
                    )
                return result

            result = self.state.paginate(name + a.get("path", "") + a.get("target", ""), members, a)
            try:
                result["signature"] = str(inspect.signature(target))
            except (ValueError, TypeError):
                result["signature"] = None
            result["documentation"] = (inspect.getdoc(target) or "")[:3000]
            result["documentation_truncated"] = len(inspect.getdoc(target) or "") > 3000
            if hasattr(target, "__members__"):
                result["enum_members"] = list(target.__members__)
            return result
        if name == "api.call":
            if "set" in a:
                parent, _, attr = a["path"].rpartition(".")
                if attr.startswith("_") or not attr.isidentifier():
                    raise Fault("INVALID_API_PATH", "Only public properties may be set")
                target = self.public(parent, a.get("target"))
                setattr(target, attr, self.decode(a["set"]))
                return self.encode(getattr(target, attr))
            target = self.public(a["path"], a.get("target"))
            result = (
                target
                if a.get("get")
                else target(*self.decode(a.get("args", [])), **self.decode(a.get("kwargs", {})))
            )
            return self.encode(result)
        raise Fault("UNKNOWN_OPERATION", name)

    def js_json(self, expression):
        value = self.sp.js.evaluate("JSON.stringify(" + expression + ")")
        return json.loads(value) if value else None

    def undo_scope(self, name):
        return self.sp.layerstack.ScopedModification(name)

    def capture(self, args):
        from PySide6.QtCore import QBuffer, QIODevice, Qt
        from PySide6.QtGui import QImage

        kind = args.get("image", "none")
        width = args.get("width", 960)
        if kind == "none":
            return [], {"kind": "none"}
        if kind == "window":
            window = self.sp.ui.get_main_window()
            if not window.isVisible() or window.isMinimized():
                raise Fault(
                    "CAPTURE_UNAVAILABLE",
                    "Qt window capture requires a visible, non-minimized Painter window",
                )
            screen = window.screen()
            image = screen.grabWindow(int(window.winId())).toImage()
            metadata = {
                "kind": "window",
                "fallback": True,
                "includes_ui": True,
                "coherence": "GPU pixels may lag facts; no render-completion fence",
            }
        else:
            stack = self.stack(args)
            channel = args.get("channel", "BaseColor")
            self.public("textureset.ChannelType." + channel)
            # Adobe documentMap identifiers are lower camel case, except normal uses 'normal'.
            channel_id = channel[0].lower() + channel[1:]
            with tempfile.TemporaryDirectory(prefix="painter-mcp-preview-") as directory:
                config = {
                    "exportPath": directory,
                    "exportShaderParams": False,
                    "exportList": [{"rootPath": str(stack)}],
                    "defaultExportPreset": "mcp-preview",
                    "exportPresets": [
                        {
                            "name": "mcp-preview",
                            "maps": [
                                {
                                    "fileName": "preview_$udim",
                                    "channels": [
                                        {
                                            "destChannel": c,
                                            "srcChannel": c,
                                            "srcMapType": "documentMap",
                                            "srcMapName": channel_id,
                                        }
                                        for c in "RGB"
                                    ],
                                }
                            ],
                        }
                    ],
                    "exportParameters": [
                        {
                            "parameters": {
                                "fileFormat": "png",
                                "bitDepth": "8",
                                "dithering": False,
                                "paddingAlgorithm": "infinite",
                                "sizeLog2": min(10, max(7, int(math.ceil(math.log2(width))))),
                            }
                        }
                    ],
                }
                exported = self.sp.export.export_project_textures(config)
                if exported.status != self.sp.export.ExportStatus.Success:
                    raise Fault("CAPTURE_UNAVAILABLE", exported.message)
                paths = [p for values in exported.textures.values() for p in values]
                if not paths:
                    raise Fault("CAPTURE_UNAVAILABLE", "No channel preview was exported")
                image = QImage(paths[0]).copy()
                metadata = {
                    "kind": "texture",
                    "channel": channel,
                    "tile_count": len(paths),
                    "shown_tile": Path(paths[0]).stem,
                    "truncated": len(paths) > 1,
                    "coherence": "native export; image is one channel/tile, not shaded 3D",
                }
        if image.isNull():
            raise Fault("CAPTURE_UNAVAILABLE", "Qt returned an empty image")
        image = image.scaled(
            width,
            width,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
        if len(encoded) > MAX_IMAGE:
            raise Fault("IMAGE_LIMIT", "Image exceeds 2 MB encoded; request a smaller width")
        metadata.update(
            width=image.width(), height=image.height(), bytes=len(encoded), image_index=1
        )
        return [{"type": "image", "mimeType": "image/png", "data": encoded}], metadata
