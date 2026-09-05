import asyncio
import json
import sys
import threading
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from painter_mcp.catalog import CORE, tools_list
from painter_mcp.transport import Transport


def test_official_mcp_stdio_initialize_list_call_and_shutdown(broker, tmp_path):
    transport = Transport(broker)
    transport.start()
    path = tmp_path / "connection.json"
    path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": transport.port,
                "token": transport.token,
                "runtime_id": broker.runtime_id,
            }
        ),
        encoding="utf-8",
    )
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            broker.pump()
            time.sleep(0.001)

    app_thread = threading.Thread(target=pump)
    app_thread.start()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "painter_mcp", "serve"],
            env={"PAINTER_MCP_CONNECTION": str(path)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "painter-mcp"
                listed = await session.list_tools()
                assert {t.name for t in listed.tools} == set(CORE)
                assert len(listed.tools) == 8
                health = await session.call_tool("painter_status", {})
                assert not health.isError
                assert health.structuredContent["data"]["runtime_id"] == broker.runtime_id
                result = await session.call_tool(
                    "painter_run",
                    {
                        "steps": [
                            {
                                "id": "a",
                                "op": "layers.create",
                                "args": {"kind": "fill"},
                                "select": ["/ref"],
                            }
                        ],
                        "observe": {"image": "texture"},
                    },
                )
                assert not result.isError
                assert result.content[1].type == "image"
                assert result.structuredContent["data"]["steps"][0]["result"]["/ref"]
                # Invalid arguments are SDK tool errors, not process crashes.
                invalid = await session.call_tool("painter_run", {"steps": "wrong"})
                assert invalid.isError
                assert not (await session.call_tool("painter_status", {})).isError

    try:
        asyncio.run(exercise())
    finally:
        stop.set()
        app_thread.join(2)
        transport.close()


def test_stdio_lists_tools_without_painter(tmp_path):
    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "painter_mcp", "serve"],
            env={"PAINTER_MCP_CONNECTION": str(tmp_path / "absent.json")},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 8
                result = await session.call_tool("painter_status", {})
                assert result.isError
                assert result.structuredContent["data"]["error"]["code"] == "NOT_CONNECTED"

    asyncio.run(exercise())


def test_all_tool_schemas_are_valid_json_schema():
    from jsonschema import Draft202012Validator

    for tool in tools_list():
        Draft202012Validator.check_schema(tool["inputSchema"])
    Draft202012Validator(CORE["painter_run"][1]).validate(
        {
            "steps": [{"id": "a", "op": "project.info"}],
            "observe": {"texture_set": {"$ref": "a#/ref"}},
        }
    )
