import pytest

from painter_mcp.common import Fault, dumps

from .conftest import execute


def test_batch_refs_use_unprojected_results(engine, adapter):
    result = execute(
        engine,
        "painter_run",
        {
            "steps": [
                {"id": "a", "op": "layers.create", "args": {"kind": "fill"}, "select": []},
                {
                    "id": "b",
                    "op": "layers.update",
                    "args": {"node": {"$ref": "a#/ref"}, "name": "renamed"},
                },
            ]
        },
    )
    assert not result["isError"]
    records = result["structuredContent"]["data"]["steps"]
    assert records[0]["result"] == {}
    assert adapter.calls[1][1]["node"].endswith(":1")


def test_partial_failure_reports_success_failed_skipped(engine, adapter):
    result = execute(
        engine,
        "painter_run",
        {
            "steps": [
                {"id": "a", "op": "layers.create", "args": {"kind": "fill"}},
                {"id": "b", "op": "layers.delete", "args": {"node": {"$ref": "a#/ref"}}},
                {"id": "c", "op": "layers.create", "args": {"kind": "fill"}},
            ],
            "observe": {"image": "texture"},
            "undo": "layerstack",
        },
    )
    data = result["structuredContent"]["data"]
    assert result["isError"]
    assert [x["state"] for x in data["steps"]] == ["completed", "failed", "skipped"]
    assert data["steps"][1]["side_effects_possible"]
    assert data["undo"]["rolled_back"] is False
    assert adapter.value == 1
    assert adapter.scope_depth == 0
    assert result["content"][1]["type"] == "image"


@pytest.mark.parametrize(
    "steps",
    [
        [
            {"id": "a", "op": "layers.create", "args": {"kind": "fill"}},
            {"id": "a", "op": "project.info"},
        ],
        [{"id": "a", "op": "layers.update", "args": {"node": {"$ref": "b#/ref"}}}],
        [{"id": "a", "op": "layers.create", "args": {"kind": "imaginary"}}],
        [{"id": "a", "op": "layers.create", "args": {"kind": "fill", "unknown": True}}],
        [{"id": "a", "op": "invented.operation"}],
    ],
)
def test_preflight_rejects_invalid_batches_before_any_edits(engine, adapter, steps):
    with pytest.raises(Fault):
        execute(engine, "painter_run", {"steps": steps})
    assert adapter.calls == []


def test_undo_refuses_non_layerstack_mutations(engine, adapter):
    with pytest.raises(Fault, match="Grouped undo"):
        execute(
            engine,
            "painter_run",
            {"steps": [{"id": "a", "op": "project.close"}], "undo": "layerstack"},
        )
    assert adapter.calls == []


def test_output_projection_error_does_not_erase_completed_edit(engine):
    result = execute(
        engine,
        "painter_run",
        {
            "steps": [
                {"id": "a", "op": "layers.create", "args": {"kind": "fill"}, "select": ["/absent"]}
            ]
        },
    )
    step = result["structuredContent"]["data"]["steps"][0]
    assert step["state"] == "completed"
    assert step["presentation_error"]["code"] == "INVALID_REFERENCE"


def test_capture_failure_preserves_edit_success(engine, adapter):
    adapter.capture_error = True
    result = execute(
        engine,
        "painter_run",
        {
            "steps": [{"id": "a", "op": "layers.create", "args": {"kind": "fill"}}],
            "observe": {"image": "window"},
        },
    )
    assert not result["isError"]
    assert result["structuredContent"]["data"]["observation"]["data"]["image"]["captured"] is False
    assert adapter.value == 1


def test_scoped_guard_and_changes(engine, adapter):
    observation = execute(engine, "painter_observe", {})["structuredContent"]["data"]
    token = observation["cursor"]
    unchanged = execute(engine, "painter_changes", {"cursor": token})
    assert not unchanged["structuredContent"]["data"]["changed"]
    adapter.value += 1
    changed = execute(engine, "painter_changes", {"cursor": token})
    assert changed["structuredContent"]["data"]["requires_observation"]
    with pytest.raises(Fault) as error:
        execute(
            engine,
            "painter_run",
            {"if_observation": token, "steps": [{"id": "a", "op": "project.info"}]},
        )
    assert error.value.code == "STALE_OBSERVATION"


def test_session_python_sdk_and_reset(engine, state):
    token = state.session("open")["session_id"]
    first = execute(
        engine,
        "painter_script",
        {
            "session_id": token,
            "source": "x = 41\nhandle = painter.keep({'x':x})\nold_sdk=painter\nresult=handle",
        },
    )
    assert not first["isError"]
    second = execute(
        engine,
        "painter_script",
        {"session_id": token, "source": "result = painter.get(handle)['x'] + 1"},
    )
    assert second["structuredContent"]["data"]["result"] == 42
    stale = execute(
        engine, "painter_script", {"session_id": token, "source": "old_sdk.call('project.info')"}
    )
    assert stale["structuredContent"]["data"]["error"]["code"] == "STALE_CELL"
    state.session("reset", token)
    assert not state.sessions[token]["namespace"]
    state.reset_project()
    with pytest.raises(Fault):
        state.session("status", token)


def test_script_stdout_bounded_and_result_not_reused(engine, state):
    token = state.session("open")["session_id"]
    first = execute(
        engine, "painter_script", {"session_id": token, "source": "print('x'*100000)\nresult=8"}
    )
    assert len(first["structuredContent"]["data"]["stdout"]) == 8192
    assert first["structuredContent"]["data"]["stdout_truncated"]
    second = execute(engine, "painter_script", {"session_id": token, "source": "pass"})
    assert second["structuredContent"]["data"]["result"] is None


def test_script_exception_and_systemexit_contained(engine):
    for code in ("raise ValueError('oops')", "raise SystemExit(3)"):
        result = execute(engine, "painter_script", {"source": code, "observe": {}})
        assert result["isError"]
        assert result["structuredContent"]["data"]["side_effects_possible"]
        assert result["structuredContent"]["data"]["observation"]["ok"]


def test_sdk_keep_limit_and_call_budget(engine):
    result = execute(engine, "painter_script", {"source": "for i in range(65): painter.keep(i)"})
    assert result["structuredContent"]["data"]["error"]["code"] == "KEEP_LIMIT"
    result = execute(
        engine, "painter_script", {"source": "for i in range(130): painter.call('project.info')"}
    )
    assert result["structuredContent"]["data"]["error"]["code"] == "SDK_CALL_LIMIT"


def test_response_byte_limit_is_explicit_and_does_not_change_execution_success(engine):
    result = execute(engine, "painter_script", {"source": "result='x'*100000"})
    assert result["structuredContent"]["data"]["truncated"]
    assert not result["isError"]
    assert len(dumps(result).encode()) < 1000


def test_nested_post_observation_reference_schema(engine):
    engine.preflight(
        "painter_run",
        {
            "steps": [{"id": "a", "op": "project.info"}],
            "observe": {"texture_set": {"$ref": "a#/texture_set"}},
        },
    )
