import pytest
from jsonschema import Draft202012Validator, ValidationError

from painter_mcp.catalog import OPS
from painter_mcp.common import Fault, validate

from .conftest import execute


@pytest.mark.parametrize(
    "name,args",
    [
        ("layers.create", {"kind": "smart_material"}),
        ("layers.create", {"kind": "instance"}),
        ("project.save", {"copy": True}),
        ("resources.shelves", {"action": "add", "name": "missing-path"}),
        ("resources.import", {"location": "shelf", "path": "test", "usage": "TEXTURE"}),
        ("project.metadata", {"context": "test", "value": 3}),
        ("baking.link", {"unlink": False}),
        ("sources.set", {"node": "test", "color": [1]}),
    ],
)
def test_conditional_requirements_prevent_earlier_edits(engine, adapter, name, args):
    with pytest.raises(Fault):
        execute(
            engine,
            "painter_run",
            {
                "steps": [
                    {"id": "first", "op": "layers.create", "args": {"kind": "group"}},
                    {"id": "invalid", "op": name, "args": args},
                ]
            },
        )
    assert not adapter.calls
    with pytest.raises(ValidationError):
        Draft202012Validator(OPS[name]["inputSchema"]).validate(args)


def test_specialist_schemas_are_standard_json_schema():
    for spec in OPS.values():
        Draft202012Validator.check_schema(spec["inputSchema"])
    validate({"unlink": True}, OPS["baking.link"]["inputSchema"])
    validate({"context": "test"}, OPS["project.metadata"]["inputSchema"])
