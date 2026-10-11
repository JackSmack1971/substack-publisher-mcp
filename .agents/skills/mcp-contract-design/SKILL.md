---
name: mcp-contract-design
description: Design MCP tools, resources, prompts and risk/authorization contracts before implementation or migration.
---

# Mcp Contract Design

1. Convert each user goal into one minimally scoped MCP feature. 2. Define canonical tool name, purpose, read/write risk, input/output schemas, failure modes, max payload, idempotency, quotas, upstream calls, credential scope, tenancy and auth requirements. 3. Separate resources from tools and prompts; prefer stable deterministic tools/list. 4. Record user-facing annotations as descriptive hints only. 5. Explicitly decide modern protocol support and any legacy fallback; do not invent unsupported extensions. 6. Fill `docs/contracts/server-contract.md` and require a contract review before code.

Reference: [`references/checklist.md`](references/checklist.md) for the gate-specific review checklist. Use the latest official SDK and specification for API details. Never infer test success.
