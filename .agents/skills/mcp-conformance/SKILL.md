---
name: mcp-conformance
description: Verify real MCP wire-protocol behavior, client interoperability, schemas, permissions and failure handling.
---

# Mcp Conformance

1. Select and document supported protocol revisions and actual host versions. 2. Test 2026 server/discover, stateless requests, routing headers, tool discovery, calls, errors, cancellations and concurrent request isolation. 3. If 2025 compatibility is promised, test initialize, session behavior and legacy client separately. 4. Validate JSON schemas, structured output, stable list order, invalid arguments and error mapping. 5. Test authorization failures (missing/expired/wrong-audience/no scope/other tenant) and benign fixture calls. 6. Run Inspector/SDK test client and any available protocol conformance tests. Record exact commands/output and skips in `docs/verification/evidence.md`.

Reference: [`references/checklist.md`](references/checklist.md) for the gate-specific review checklist. Use the latest official SDK and specification for API details. Never infer test success.
