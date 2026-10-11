---
name: mcp-discovery
description: Research or audit an MCP server, protocol revision, SDK, client compatibility, and upstream API before architectural decisions.
---

# Mcp Discovery

1. Inventory repository, toolchain, Git state and actual target MCP hosts. 2. Open the pinned MCP 2026-07-28 specification and current SDK reference; verify any differing host requirements. 3. Establish desired modern/legacy compatibility, stdio/HTTP modes, upstream quotas, licensing, auth, privacy and data freshness. 4. Produce `docs/architecture/architecture.md` using template; note verified URLs/dates and decisions. 5. Stop before adding dependencies or coding until material compatibility/security decisions are made.

Reference: [`references/checklist.md`](references/checklist.md) for the gate-specific review checklist. Use the latest official SDK and specification for API details. Never infer test success.
