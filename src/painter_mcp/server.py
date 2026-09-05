"""MCP stdio server using the official SDK's lifecycle/protocol implementation."""

import asyncio

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .catalog import tools_list
from .client import Client


async def serve(client=None):
    client = client or Client()
    server = Server("painter-mcp", version=__version__)

    @server.list_tools()
    async def list_tools():
        return [types.Tool(**tool) for tool in tools_list()]

    @server.call_tool()
    async def call_tool(name, arguments):
        result = await asyncio.to_thread(client.call_tool, name, arguments)
        return types.CallToolResult(**result)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    asyncio.run(serve())
