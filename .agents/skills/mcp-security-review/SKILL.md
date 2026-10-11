---
name: mcp-security-review
description: Audit an MCP server for OAuth, object-level authorization, injection, SSRF, secrets, dangerous actions and tenant isolation.
---

# Mcp Security Review

1. Trace data and identities from host/client through HTTP/stdin, handler, token validation, upstream, and response. 2. Check auth metadata, issuer/audience, expiration, scopes and per-resource owner checks; no token passthrough. 3. Model malicious tool arguments, prompt injection from resources, client-supplied URLs and hostnames, header smuggling, path traversal, replay and concurrent calls. 4. Confirm hints do not grant access, write operations need server policy, and error/log outputs redact secrets. 5. Demand negative-path tests and rank findings by impact/evidence; do not edit in review.

Reference: [`references/checklist.md`](references/checklist.md) for the gate-specific review checklist. Use the latest official SDK and specification for API details. Never infer test success.
