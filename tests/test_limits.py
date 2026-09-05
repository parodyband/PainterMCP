from painter_mcp.client import render_receipt
from painter_mcp.common import MAX_FACTS, dumps, envelope


def test_large_partial_result_preserves_which_edits_completed():
    result = envelope(
        {
            "steps": [
                {"id": "a", "state": "completed", "result": "x" * 100000, "undo": "layerstack"},
                {
                    "id": "b",
                    "state": "failed",
                    "error": {"code": "NATIVE_ERROR", "message": "Error", "huge": "x" * 100000},
                    "side_effects_possible": True,
                },
                {"id": "c", "state": "skipped"},
            ],
            "undo": {"rolled_back": False},
        },
        ok=False,
    )
    data = result["structuredContent"]["data"]
    assert data["truncated"] and result["isError"]
    assert [s["state"] for s in data["steps"]] == ["completed", "failed", "skipped"]
    assert data["steps"][1]["error"]["code"] == "NATIVE_ERROR"
    assert data["steps"][1]["side_effects_possible"]
    assert len(dumps(result["structuredContent"]).encode()) <= MAX_FACTS


def test_recovery_metadata_cannot_bypass_fact_budget():
    execution = envelope({"result": "x" * 65000})
    recovered = render_receipt(
        {"result": execution, "request_id": "x" * 4096, "state": "completed"}
    )
    assert len(dumps(recovered["structuredContent"]).encode()) <= MAX_FACTS
    assert recovered["structuredContent"]["data"]["request"]["state"] == "completed"


def test_post_observation_image_indices_after_sdk_image(engine):
    from .conftest import execute

    result = execute(
        engine,
        "painter_script",
        {"source": "painter.observe(image='texture')", "observe": {"image": "texture"}},
    )
    assert len(result["content"]) == 3
    data = result["structuredContent"]["data"]
    assert data["observation_image_indices"] == [2]
    assert data["observation"]["data"]["image"]["image_index"] == 2


def test_kept_results_enforce_utf8_bytes_not_character_count(engine):
    from .conftest import execute

    result = execute(engine, "painter_script", {"source": "painter.keep(chr(0x1F600) * 1100000)"})
    assert result["structuredContent"]["data"]["error"]["code"] == "KEEP_LIMIT"


def test_page_cache_enforces_utf8_byte_budget(state):
    items = [chr(0x1F600) * 3000] * 100
    for index in range(7):
        state.paginate(str(index), lambda: items, {"limit": 1})
    assert sum(page["size"] for page in state.pages.values()) <= 8_000_000
    assert len(state.pages) == 6
