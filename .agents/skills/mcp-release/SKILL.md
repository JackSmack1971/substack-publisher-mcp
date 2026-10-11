---
name: mcp-release
description: Prepare an MCP server for gated release with reproducible artifacts, protocol compatibility evidence and rollback plan.
---

# Mcp Release

1. Confirm source/test/review state and required decision records. 2. Require a passing documented conformance matrix and security test suite; explicitly label any missing checks. 3. Review dependency lock/pins, licensing, secret handling, container/stdio packaging, provenance, observability and rollback/upgrade strategy. 4. Compare published tool catalog to prior version and flag breaking changes. 5. Produce release readiness decision in `docs/verification/evidence.md`. 6. Do not deploy, push, publish or create releases without a separate explicit authorization and permitted runtime capability.

Reference: [`references/checklist.md`](references/checklist.md) for the gate-specific review checklist. Use the latest official SDK and specification for API details. Never infer test success.
