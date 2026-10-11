"""Start the installed CLI and exercise the real MCP stdio transport offline."""

import os
import sys
from pathlib import Path

import anyio
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_installed_server_initializes_and_lists_tools():
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUBSTACK_")}
    command = str(Path(sys.executable).with_name(
        "substack-publisher-mcp.exe" if os.name == "nt" else "substack-publisher-mcp"
    ))
    with anyio.fail_after(20):
        async with stdio_client(StdioServerParameters(command=command, env=env)) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == "substack-publisher-mcp"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "list_drafts", "list_published", "get_draft", "get_sections",
                    "get_subscriber_count", "create_draft", "update_draft",
                    "publish_draft", "schedule_draft", "unschedule_draft",
                    "delete_draft", "add_tags",
                }
                assert len(tools.tools) == 12
                schemas = {tool.name: tool.input_schema for tool in tools.tools}
                assert "allow_conflict" in schemas["update_draft"]["properties"]
                assert "confirm_token" in schemas["publish_draft"]["properties"]
                mutating_tools = {
                    "create_draft", "update_draft", "publish_draft",
                    "schedule_draft", "unschedule_draft", "delete_draft",
                    "add_tags",
                }
                for name in mutating_tools:
                    assert "confirm_token" in schemas[name]["properties"]
                    assert "confirm_token" not in schemas[name].get("required", [])
                assert set(schemas["create_draft"]["required"]) == {"title", "markdown"}
                assert set(schemas["publish_draft"]["required"]) == {"draft_id"}
                assert set(schemas["schedule_draft"]["required"]) == {
                    "draft_id", "publish_at_iso"
                }
                assert {"send", "share_automatically"} <= set(
                    schemas["publish_draft"]["properties"]
                )


async def test_modern_discovery_advertises_bounded_schemas_and_tool_risks():
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUBSTACK_")}
    command = str(Path(sys.executable).with_name(
        "substack-publisher-mcp.exe" if os.name == "nt" else "substack-publisher-mcp"
    ))
    with anyio.fail_after(20):
        async with Client(StdioServerParameters(command=command, env=env)) as client:
            assert client.protocol_version == "2026-07-28"
            result = await client.list_tools()
            assert result.cache_scope == "private"
            assert result.ttl_ms == 0
            tools = {tool.name: tool for tool in result.tools}
            assert len(tools) == 12

            create_schema = tools["create_draft"].input_schema
            assert create_schema["properties"]["title"]["maxLength"] == 300
            assert create_schema["properties"]["markdown"]["maxLength"] == 200_000
            assert create_schema["properties"]["audience"]["enum"] == [
                "everyone", "only_paid", "founding", "only_free",
            ]
            tag_array = create_schema["properties"]["tags"]["anyOf"][0]
            assert tag_array["maxItems"] == 100
            assert tag_array["items"]["maxLength"] == 128
            assert tools["get_draft"].input_schema["properties"]["draft_id"]["maxLength"] == 64
            assert tools["get_draft"].input_schema["properties"]["draft_id"]["pattern"] == r"^[A-Za-z0-9_-]+$"
            assert tools["list_drafts"].input_schema["properties"]["limit"]["maximum"] == 1000
            assert tools["schedule_draft"].input_schema["properties"]["publish_at_iso"]["maxLength"] == 64

            read_tools = {
                "list_drafts", "list_published", "get_draft", "get_sections",
                "get_subscriber_count",
            }
            for name, tool in tools.items():
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is (name in read_tools)
                assert tool.annotations.open_world_hint is True
            assert tools["delete_draft"].annotations.destructive_hint is True
            assert tools["add_tags"].annotations.destructive_hint is False
            assert "does not prove that a human approved" in client.instructions
