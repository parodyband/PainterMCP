"""Licensed acceptance suite. Refuses any pre-existing project and cleans up only its own work."""

from __future__ import annotations

import base64
import json
import os
import statistics
import time
import uuid
from pathlib import Path

from painter_mcp.client import Client, render_receipt
from painter_mcp.common import dumps


class Harness:
    def __init__(self, directory):
        self.client = Client()
        self.directory = directory
        self.records = []
        self.covered = set()
        self.images = []
        self.benchmarks = {}

    def tool(self, name, args, expect_error=False):
        started = time.perf_counter()
        receipt = self.client.submit(name, args)
        rounds = 1
        deadline = time.monotonic() + 120
        while receipt["state"] in ("queued", "running"):
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"Timed out; recover {receipt['request_id']} before continuing"
                )
            time.sleep(0.1)
            receipt = self.client.status(receipt["request_id"])
            rounds += 1
        result = render_receipt(receipt)
        labels = [s["op"] for s in args.get("steps", [])] if name == "painter_run" else [name]
        self.records.append(
            {
                "operations": labels,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "round_trips": rounds,
                "response_bytes": len(dumps(result).encode()),
                "is_error": result["isError"],
                "expected_error": expect_error,
            }
        )
        if result["isError"] != expect_error:
            raise AssertionError(
                dumps({"tool": name, "args": args, "result": result["structuredContent"]})
            )
        if not expect_error:
            self.covered.update(labels)
        for block in result["content"]:
            if block["type"] == "image":
                path = self.directory / f"capture-{len(self.images)}.png"
                path.write_bytes(base64.b64decode(block["data"]))
                self.images.append(path.name)
        return result["structuredContent"]["data"]

    def batch(self, steps, **options):
        return self.tool("painter_run", {"steps": steps, **options})

    def op(self, op, **args):
        data = self.batch([{"id": "action", "op": op, "args": args}])
        return data["steps"][0]["result"]

    def script(self, source, **options):
        return self.tool("painter_script", {"source": source, **options})

    def write_report(self, status, version):
        self.directory.mkdir(parents=True, exist_ok=True)
        report = {
            "status": status,
            "painter_version": version,
            "operation_count": len(self.covered),
            "covered_operations": sorted(self.covered),
            "requests": self.records,
            "images": self.images,
            "benchmarks": self.benchmarks,
            "summary": {
                "requests": len(self.records),
                "round_trips": sum(r["round_trips"] for r in self.records),
                "median_latency_ms": round(
                    statistics.median(r["latency_ms"] for r in self.records), 3
                )
                if self.records
                else 0,
                "total_response_bytes": sum(r["response_bytes"] for r in self.records),
            },
        }
        (self.directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def validate_painter(directory=None):
    if os.environ.get("PAINTER_MCP_TEST_ISOLATED") != "1":
        raise RuntimeError(
            "Set PAINTER_MCP_TEST_ISOLATED=1 only for a disposable, licensed Painter session with no open project"
        )
    root = Path(__file__).resolve().parents[1]
    directory = Path(directory or root / ".local/live-validation" / uuid.uuid4().hex).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    harness = h = Harness(directory)
    version = h.client.health()["capabilities"]["painter_version"]
    owner = uuid.uuid4().hex
    mesh = root / "tests/fixtures/cube.obj"
    info = h.op("project.info")
    if info["open"]:
        raise RuntimeError(
            "An existing project is open. No test project was created and nothing was closed."
        )
    shelf = directory / "shelf"
    shelf.mkdir()
    h.op("resources.shelves", action="add", name="mcp_test_" + owner, path=str(shelf))
    h.op("resources.shelves", action="remove", name="mcp_test_" + owner)
    status = "failed"
    owned = False
    try:
        h.op(
            "project.create",
            mesh=str(mesh),
            settings={
                "default_texture_resolution": 256,
                "normal_map_format": {"$enum": "project.NormalMapFormat.OpenGL"},
            },
        )
        h.op("project.metadata", context="painter-mcp-tests", key="owner", value=owner)
        owned = True
        observed = h.tool("painter_observe", {"image": "none"})
        ts = observed["texture_set"]["ref"]
        assert observed["project"]["open"] and observed["channels"]
        h.op("texturesets.list", limit=1)
        updated = h.op(
            "texturesets.update",
            texture_set=ts,
            name="MCP_Test",
            resolution=[256, 256],
            activate=True,
        )
        assert updated["ref"] == ts and updated["name"] == "MCP_Test"
        h.op("channels.add", channel="Opacity", format="L8")
        h.op("channels.edit", channel="Opacity", format="L16")
        assert any(c["type"] == "Opacity" for c in h.op("channels.list"))
        h.op("channels.remove", channel="Opacity")

        batch = h.batch(
            [
                {
                    "id": "group",
                    "op": "layers.create",
                    "args": {"kind": "group", "name": "Acceptance group"},
                    "select": ["/ref"],
                },
                {
                    "id": "fill",
                    "op": "layers.create",
                    "args": {
                        "kind": "fill",
                        "name": "Base coat",
                        "position": {
                            "node": {"$ref": "group#/ref"},
                            "where": "inside",
                            "stack_kind": "Substack",
                        },
                    },
                },
                {
                    "id": "color",
                    "op": "sources.set",
                    "args": {
                        "node": {"$ref": "fill#/ref"},
                        "channel": "BaseColor",
                        "color": [0.12, 0.3, 0.6],
                    },
                    "select": [],
                },
            ],
            undo="layerstack",
            observe={"image": "texture", "channel": "BaseColor", "width": 512},
        )
        assert batch["undo"]["grouped"] and not batch["undo"]["rolled_back"]
        assert batch["observation"]["data"]["image"].get("kind") == "texture", batch
        group = batch["steps"][0]["result"]["/ref"]
        fill = batch["steps"][1]["result"]["ref"]
        h.op(
            "layers.update",
            node=fill,
            name="Renamed base coat",
            opacity=0.8,
            channel="BaseColor",
            blending="Normal",
        )
        assert h.op("sources.get", node=fill, channel="BaseColor")["type"] == "SourceUniformColor"
        h.op("layers.select", nodes=[fill], selection_type="Content")
        h.op("masks.update", node=fill, action="add", background="White")
        h.op("masks.update", node=fill, action="update", enabled=False, background="Black")
        h.op("masks.update", node=fill, action="update", enabled=True, background="White")
        mask_fill = h.op(
            "layers.create",
            kind="fill",
            position={"node": fill, "where": "inside", "stack_kind": "Mask"},
        )["ref"]
        h.op("sources.set", node=mask_fill, color=[1, 1, 1])
        h.op("layers.projection", node=fill, mode="Triplanar")
        h.op("layers.projection", node=fill, symmetry=True)
        h.op("layers.projection", node=fill, mode="UV")
        projection = h.op(
            "layers.projection", node=fill, parameters={"uv_transformation": {"scale": [2.0, 2.0]}}
        )
        assert projection["parameters"]["uv_transformation"]["scale"] == [2.0, 2.0]
        h.op(
            "masks.update",
            node=fill,
            action="update",
            geometry_type="Mesh",
            meshes=["MCP_Test_Cube"],
        )
        paint = h.op("layers.create", kind="paint", position={"node": group, "where": "inside"})[
            "ref"
        ]
        instance = h.op("layers.create", kind="instance", source=fill)["ref"]
        h.op("layers.delete", node=instance)
        levels = h.op(
            "layers.create",
            kind="levels",
            position={"node": fill, "where": "inside", "stack_kind": "Content"},
        )["ref"]
        levels_params = h.op("layers.effect", node=levels, channel="BaseColor")
        gamma = [1.2, 1.2, 1.2] if isinstance(levels_params["gamma"], list) else 1.2
        actual_gamma = h.op("layers.effect", node=levels, parameters={"gamma": gamma})["gamma"]
        assert all(
            abs(value - 1.2) < 1e-5
            for value in (actual_gamma if isinstance(actual_gamma, list) else [actual_gamma])
        )
        anchor = h.op(
            "layers.create",
            kind="anchor",
            name="Test anchor",
            position={"node": fill, "where": "inside", "stack_kind": "Content"},
        )["ref"]
        assert anchor
        for kind in ("color_selection", "compare_mask"):
            effect = h.op(
                "layers.create",
                kind=kind,
                position={"node": fill, "where": "inside", "stack_kind": "Mask"},
            )["ref"]
            h.op("layers.effect", node=effect)
            h.op("layers.delete", node=effect)
        h.op("layers.list", limit=2)
        page = h.op("layers.list", limit=2)
        if page["next_cursor"]:
            assert h.op("layers.list", cursor=page["next_cursor"], limit=2)["offset"] == 2

        # Build resources from our own authored project and image; no proprietary assets are distributed.
        h.op(
            "materials.save",
            node=group,
            name="MCP generated material",
            kind="material",
            path=str(directory),
        )
        smart = h.op(
            "resources.import",
            path=str(directory / "MCP generated material.spsm"),
            usage="SMART_MATERIAL",
        )
        smart_layer = h.op("layers.create", kind="smart_material", resource=smart["url"])["ref"]
        h.op(
            "materials.save", node=fill, name="MCP generated mask", kind="mask", path=str(directory)
        )
        smart_mask = h.op(
            "resources.import", path=str(directory / "MCP generated mask.spmsk"), usage="SMART_MASK"
        )
        h.op("masks.update", node=paint, action="add", background="White")
        h.op(
            "layers.create",
            kind="smart_mask",
            resource=smart_mask["url"],
            position={"node": paint, "where": "inside", "stack_kind": "Mask"},
        )
        h.op("layers.delete", node=smart_layer)
        h.op("masks.update", node=paint, action="remove")
        h.op(
            "resources.import",
            path=str(directory / h.images[0]),
            usage="TEXTURE",
            location="session",
            name="mcp_test_session",
        )
        resource = h.op(
            "resources.import",
            path=str(directory / h.images[0]),
            usage="TEXTURE",
            name="mcp_test_color",
        )
        resource2 = h.op(
            "resources.import",
            path=str(directory / h.images[0]),
            usage="TEXTURE",
            name="mcp_test_color_copy",
        )
        h.op("sources.set", node=fill, channel="BaseColor", resource=resource["url"])
        h.op("resources.update", old=resource["url"], new=resource2["url"])
        assert h.op("resources.search", query="s:project!", limit=2)["total"] >= 2
        h.op("resources.shelves")

        # Resource inventory yields a real material, generator and filter when shipped assets are present.
        for usage, kind in (
            ("BASE_MATERIAL", "material"),
            ("GENERATOR", "generator"),
            ("FILTER", "filter"),
        ):
            found = h.script(
                "import substance_painter as sp\nresult = next((r.identifier().url() for r in sp.resource.search('') if sp.resource.Usage."
                + usage
                + " in r.usages()), None)"
            )["result"]
            if found:
                if kind == "material":
                    h.op("sources.set", node=fill, material=True, resource=found)
                    source = h.op("sources.get", node=fill, material=True)
                    if source.get("parameters"):
                        key, value = next(iter(source["parameters"].items()))
                        h.op("sources.set", node=fill, material=True, parameters={key: value})
                else:
                    h.op(
                        "layers.create",
                        kind=kind,
                        resource=found,
                        position={"node": fill, "where": "inside", "stack_kind": "Mask"},
                    )

        h.op("viewport.get")
        h.op(
            "viewport.set",
            camera={"position": [4, 3, 5], "rotation": [-20, 30, 0], "field_of_view": 45},
        )
        h.tool("painter_observe", {"image": "window", "width": 960})
        h.op("export.presets", limit=5)
        settings = h.op("settings.get")
        h.op(
            "settings.set",
            export_options={**settings["export_options"], "bitDepth": "8", "fileFormat": "png"},
        )
        export_config = {
            "exportPath": str(directory / "textures"),
            "exportShaderParams": False,
            "exportList": [{"rootPath": "MCP_Test"}],
            "defaultExportPreset": "validation",
            "exportPresets": [
                {
                    "name": "validation",
                    "maps": [
                        {
                            "fileName": "basecolor",
                            "channels": [
                                {
                                    "destChannel": c,
                                    "srcChannel": c,
                                    "srcMapType": "documentMap",
                                    "srcMapName": "baseColor",
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
                        "sizeLog2": 8,
                    }
                }
            ],
        }
        (directory / "textures").mkdir()
        planned = h.op("export.textures", config=export_config, dry_run=True)
        assert planned["planned"]
        exported = h.op("export.textures", config=export_config)
        assert exported["status"] == "Success"
        assert (directory / "textures/basecolor.png").exists()
        mesh_options = h.op("api.inspect", path="export.MeshExportOption")
        option = mesh_options["enum_members"][0]
        h.op("export.mesh", path=str(directory / "exported.obj"), option=option)

        params = h.op("baking.get")
        # Preserve every property except explicit output size and low-mesh-as-high switch, using live names.
        size_key = next(
            k for k in params["properties"] if "outputsize" in k.lower().replace("_", "")
        )
        size_value = params["properties"][size_key]["value"]
        size = [7, 7] if isinstance(size_value, list) else 7
        low_key = next((k for k in params["properties"] if "use_low" in k.lower()), None)
        values = {size_key: size}
        if low_key:
            values[low_key] = True
        h.op("baking.set", values=values, enabled_bakers=["Normal", "AO"], enabled=True)
        h.op("baking.link", texture_sets=[ts], reference=ts)
        h.op("baking.link", unlink=True)
        bake = h.op("baking.start")
        deadline = time.monotonic() + 90
        while True:
            job = h.op("job.status", job_id=bake["job_id"])
            if job["state"] != "running":
                break
            if time.monotonic() > deadline:
                raise AssertionError("Bake did not finish in 90 seconds")
            time.sleep(0.2)
        assert job["state"] == "completed", job
        baking_observation = h.tool("painter_observe", {"texture_set": ts, "image": "none"})
        assert baking_observation["texture_set"]["ref"] == ts
        h.op("job.cancel", job_id=bake["job_id"])
        h.op("viewport.set", mode="Edition")
        normal = h.op("mesh_maps.get", usage="Normal")
        assert normal
        h.op("mesh_maps.set", usage="Normal", resource=normal)

        observation = h.tool("painter_observe", {"image": "none"})
        assert not h.tool("painter_changes", {"cursor": observation["cursor"]})[
            "requires_observation"
        ]
        h.op("layers.update", node=fill, name="Guard changed")
        assert h.tool("painter_changes", {"cursor": observation["cursor"]})["requires_observation"]
        h.tool(
            "painter_run",
            {
                "if_observation": observation["observation_id"],
                "steps": [
                    {
                        "id": "x",
                        "op": "layers.update",
                        "args": {"node": fill, "name": "Must not run"},
                    }
                ],
            },
            expect_error=True,
        )
        partial = h.tool(
            "painter_run",
            {
                "steps": [
                    {
                        "id": "good",
                        "op": "layers.update",
                        "args": {"node": fill, "name": "Partial kept"},
                    },
                    {
                        "id": "bad",
                        "op": "layers.delete",
                        "args": {"node": "node:" + info["epoch"] + ":99999"},
                    },
                    {
                        "id": "skip",
                        "op": "layers.update",
                        "args": {"node": fill, "name": "Must not run"},
                    },
                ],
                "observe": {},
            },
            expect_error=True,
        )
        assert [s["state"] for s in partial["steps"]] == ["completed", "failed", "skipped"]
        start_index = len(h.records)
        for index in range(8):
            h.op("layers.update", node=fill, name=f"Benchmark coat {index}")
        h.tool("painter_observe", {"nodes": [fill], "image": "none"})
        separate = h.records[start_index:]
        start_index = len(h.records)
        h.batch(
            [
                {
                    "id": f"rename{index}",
                    "op": "layers.update",
                    "args": {"node": fill, "name": f"Benchmark coat {index}"},
                    "select": [],
                }
                for index in range(8)
            ],
            undo="layerstack",
            observe={"nodes": [fill], "image": "none"},
        )
        combined = h.records[start_index:]
        h.benchmarks["eight_renames_and_observation"] = {
            "separate": {
                "round_trips": sum(r["round_trips"] for r in separate),
                "response_bytes": sum(r["response_bytes"] for r in separate),
                "latency_ms": round(sum(r["latency_ms"] for r in separate), 3),
            },
            "batched_grouped_undo_selected_outputs": {
                "round_trips": sum(r["round_trips"] for r in combined),
                "response_bytes": sum(r["response_bytes"] for r in combined),
                "latency_ms": round(sum(r["latency_ms"] for r in combined), 3),
            },
        }
        session = h.tool("painter_session", {"action": "open"})["session_id"]
        h.script("counter=40\nhandle=painter.keep({'n':counter})", session_id=session)
        assert h.script("result=painter.get(handle)['n']+2", session_id=session)["result"] == 42
        assert (
            h.script("result = arguments.n + 1;", language="javascript", arguments={"n": 4})[
                "result"
            ]
            == 5
        )
        h.op("api.call", path="application.version")
        runtime_id = h.client.health()["runtime_id"]
        action = {
            "request_id": runtime_id + ":" + owner,
            "steps": [
                {
                    "id": "once",
                    "op": "layers.create",
                    "args": {"kind": "group", "name": "Exactly once"},
                }
            ],
        }
        first = h.tool("painter_run", action)
        second = h.tool("painter_run", action)
        assert first["steps"][0]["result"]["ref"] == second["steps"][0]["result"]["ref"]
        h.tool("painter_session", {"action": "close", "session_id": session})
        saved = directory / "test.spp"
        h.op("project.save", path=str(saved))
        h.op("project.save", path=str(directory / "copy.spp"), copy=True)
        h.op("project.close")
        h.op("project.open", path=str(saved))
        assert h.op("project.metadata", context="painter-mcp-tests", key="owner") == owner
        reload_job = h.op("project.reload_mesh", path=str(mesh))
        reloaded = h.op("job.status", job_id=reload_job["job_id"])
        assert reloaded["state"] == "completed", reloaded
        h.op("project.close", discard=True)
        h.op(
            "project.create",
            mesh=str(root / "tests/fixtures/cube_udim.obj"),
            settings={
                "default_texture_resolution": 256,
                "project_workflow": {"$enum": "project.ProjectWorkflow.UVTile"},
            },
        )
        h.op("project.metadata", context="painter-mcp-tests", key="owner", value=owner)
        udim = h.op("texturesets.list")["items"][0]
        assert len(udim["uv_tiles"]) == 2
        h.op("texturesets.update", texture_set=udim["ref"], uv_tile=[1, 0], resolution=[128, 128])
        udim_fill = h.op("layers.create", kind="fill", name="UDIM coat")["ref"]
        h.op("sources.set", node=udim_fill, channel="BaseColor", color=[0.2, 0.5, 0.1])
        h.op(
            "masks.update",
            node=udim_fill,
            action="add",
            background="White",
            geometry_type="UVTile",
            uv_tiles=[[0, 0]],
        )
        preview = h.tool(
            "painter_observe", {"nodes": [udim_fill], "image": "texture", "width": 256}
        )
        assert preview["image"]["tile_count"] == 2 and preview["image"]["truncated"]
        assert len(preview["layers"]) == 1
        status = "passed"
    finally:
        if owned:
            current = h.op("project.info")
            if current["open"]:
                actual_owner = h.op("project.metadata", context="painter-mcp-tests", key="owner")
                if actual_owner == owner:
                    h.op("project.close", discard=True)
                else:
                    raise RuntimeError(
                        "Project ownership changed; refusing cleanup of another project"
                    )
        report = harness.write_report(status, version)
        print(
            json.dumps(
                {
                    "status": status,
                    "report": str(directory / "report.json"),
                    "summary": report["summary"],
                },
                indent=2,
            ),
            flush=True,
        )
    return report


if __name__ == "__main__":
    validate_painter()
