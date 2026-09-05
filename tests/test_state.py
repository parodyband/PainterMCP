import pytest

from painter_mcp.common import Fault, pointer, resolve, validate
from painter_mcp.state import State


def test_snapshot_pagination_and_expiry():
    now = [0.0]
    state = State(clock=lambda: now[0])
    items = list(range(9))
    page = state.paginate("q", lambda: items.copy(), {"limit": 3})
    assert page["items"] == [0, 1, 2]
    items.append(10)
    second = state.paginate("q", lambda: [], {"cursor": page["next_cursor"], "limit": 3})
    assert second["items"] == [3, 4, 5]
    assert second["total"] == 9
    now[0] = 121
    with pytest.raises(Fault):
        state.paginate("q", lambda: [], {"cursor": second["next_cursor"]})


def test_byte_bounded_page_continuation(state):
    page = state.paginate("q", lambda: ["x" * 20000] * 8, {"limit": 8})
    assert len(page["items"]) == 2
    assert page["next_cursor"].endswith("/2")
    assert page["truncated"]


def test_pages_invalidated_on_project_replacement(state):
    page = state.paginate("q", lambda: [1, 2], {"limit": 1})
    state.reset_project()
    with pytest.raises(Fault):
        state.paginate("q", lambda: [], {"cursor": page["next_cursor"]})


def test_session_count_idle_expiry_and_handles():
    now = [0.0]
    state = State(clock=lambda: now[0])
    sessions = [state.session("open")["session_id"] for _ in range(16)]
    with pytest.raises(Fault):
        state.session("open")
    now[0] = 1801
    with pytest.raises(Fault):
        state.session("status", sessions[0])
    assert state.session("open")
    value = object()
    token = state.retain(value)
    assert state.dereference(token) is value
    assert state.retain(value) == token
    state.reset_project()
    with pytest.raises(Fault):
        state.dereference(token)


def test_json_pointer_escapes_and_nested_refs():
    assert pointer({"a/b": {"~": [3]}}, "/a~1b/~0/0") == 3
    assert resolve({"v": [{"$ref": "a#/id"}]}, {"a": {"id": 4}}) == {"v": [4]}
    with pytest.raises(Fault):
        resolve({"$ref": "missing#/id"}, {})


@pytest.mark.parametrize(
    "value,schema",
    [
        (True, {"type": "integer"}),
        (1.3, {"type": "integer"}),
        (-1, {"type": "number", "minimum": 0}),
        ([], {"type": "array", "minItems": 1}),
        ({"a": 1}, {"type": "object", "additionalProperties": False}),
    ],
)
def test_schema_rejects_invalid_json_types(value, schema):
    with pytest.raises(Fault):
        validate(value, schema)
