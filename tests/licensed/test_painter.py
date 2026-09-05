import os

import pytest

pytestmark = [
    pytest.mark.painter,
    pytest.mark.skipif(
        os.environ.get("PAINTER_MCP_TEST_ISOLATED") != "1",
        reason="Requires an explicitly isolated, licensed Painter desktop instance",
    ),
]


def test_licensed_acceptance():
    from scripts.validate_painter import validate_painter

    assert validate_painter()["status"] == "passed"
