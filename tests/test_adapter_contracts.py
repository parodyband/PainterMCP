"""Regressions discovered in licensed Painter 12.1.4, reproduced at the adapter boundary."""

from types import SimpleNamespace

from painter_mcp.adapter import PainterAdapter


def bare_adapter(state):
    adapter = object.__new__(PainterAdapter)
    adapter.state = state
    adapter.assert_thread = lambda: None
    return adapter


def test_source_introspection_accepts_properties_and_methods(state):
    adapter = bare_adapter(state)
    adapter.encode = lambda value: value
    source = SimpleNamespace(
        resource_id="resource://test", image_inputs=["input"], get_parameters=lambda: {"seed": 2}
    )
    info = adapter.source_info(source)
    assert info["resource_id"] == "resource://test"
    assert info["image_inputs"] == ["input"]
    assert info["parameters"]["seed"] == 2


def test_smart_mask_returns_multiple_effects(state):
    adapter = bare_adapter(state)
    effects = [object(), object()]
    adapter.sp = SimpleNamespace(
        layerstack=SimpleNamespace(insert_smart_mask=lambda *args: effects)
    )
    adapter.position = lambda args: "position"
    adapter.rid = lambda value: value
    adapter.node_info = lambda node: {"ref": str(effects.index(node))}
    result = adapter.invoke("layers.create", {"kind": "smart_mask", "resource": "resource://test"})
    assert result == {"nodes": [{"ref": "0"}, {"ref": "1"}], "kind": "smart_mask"}


def test_mask_effect_facts_do_not_query_multichannel_only_property(state):
    class Node:
        def uid(self):
            return 1

        def get_name(self):
            return "mask fill"

        def get_type(self):
            return "FillEffect"

        def is_visible(self):
            return True

        def has_blending(self):
            return False

        def is_in_mask_stack(self):
            return True

        @property
        def active_channels(self):
            raise RuntimeError("Only valid in multi channel context")

    adapter = bare_adapter(state)
    adapter.sp = SimpleNamespace(layerstack=SimpleNamespace(LayerNode=type("LayerNode", (), {})))
    assert adapter.node_info(Node())["type"] == "FillEffect"


def test_uv_projection_does_not_query_3d_symmetry(state):
    class Fill:
        def get_projection_mode(self):
            return "UV"

        def get_projection_parameters(self):
            return {}

        def is_symmetry_enabled(self):
            raise AssertionError("UV cannot query symmetry")

    adapter = bare_adapter(state)
    adapter.sp = SimpleNamespace(
        layerstack=SimpleNamespace(is_3d_projection_mode=lambda mode: False)
    )
    adapter.object = lambda token: Fill()
    adapter.encode = lambda value: value
    assert adapter.invoke("layers.projection", {"node": "test"})["symmetry"] is None
