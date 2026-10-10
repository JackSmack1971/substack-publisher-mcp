"""Start the installed CLI and exercise the real MCP stdio transport offline."""

import os
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
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
