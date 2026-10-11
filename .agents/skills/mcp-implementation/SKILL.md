---
name: mcp-implementation
description: Implement a bounded MCP server feature from an accepted protocol and tool contract using current compatible SDK APIs.
---

# Mcp Implementation

1. Review the accepted contract, SDK version and API docs and existing repository conventions. 2. Implement handler/domain/upstream boundaries without speculative dependencies. 3. Perform input validation, authorization, output shaping, bounded errors, timeouts and cancellation. 4. Implement modern 2026 negotiation deliberately, not by SDK version assumption; test any 2025 fallback as a separate contract. 5. Add deterministic unit/integration tests with fake upstreams, and test error/denial paths. 6. Run focused checks, inspect diff, and provide verification evidence. Never deploy or publish.

Reference: [`references/checklist.md`](references/checklist.md) for the gate-specific review checklist. Use the latest official SDK and specification for API details. Never infer test success.
