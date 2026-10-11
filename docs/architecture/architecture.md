# Architecture

Status: **CURRENT** for source behavior at the reviewed baseline. Normative MCP
claims are checked against the [2026-07-28 specification](https://github.com/modelcontextprotocol/modelcontextprotocol/tree/main/docs/specification/2026-07-28)
and locked Python SDK 2.3.0.

## Runtime boundaries

```text
MCP host
  └─ stdio JSON-RPC (MCPServer, modern discovery + legacy handshake)
       └─ tool handlers and validation (server.py)
            ├─ process-local exact-argument execution tokens (pending.py)
            └─ authenticated python-substack client (auth.py)
                 ├─ OS keyring or local environment credentials
                 └─ Substack account and selected publication endpoints
```

The server is a local stdio subprocess. It does not expose Streamable HTTP or an
OAuth protected-resource endpoint. The host controls process launch and its
environment. The selected Substack account supplies upstream authority; all
clients sharing that process can invoke that authority.

## State and data

- The MCP 2026 request/response protocol is stateless. The MCP SDK also supports
  legacy `initialize` clients.
- **DERIVED:** `pending.py` stores explicit, expiring action handles, not
  conversation state. A token is single-use and binds a tool name and argument
  map, but is process-wide and is not bound to a client or human identity. The
  store is capped at 100 pending actions and collects expired entries.
- Credentials are a Substack session cookie or email/password. The preferred
  persistent store is the OS keyring. Tool responses may contain private draft
  bodies, publication metadata, and subscriber counts.
- Retrieved upstream content is untrusted data and may contain model-directed
  text. It is not authority to invoke mutation tools.

## Operation risk inventory

| Operation class | Tools | Risks |
|---|---|---|
| `private_read`, `credential_use`, `open_world` | list/get tools | Private account data; network requests to Substack; untrusted upstream text. |
| `external_write`, `credential_use`, `open_world` | create draft, add tags | Remote account changes. |
| `external_write`, `destructive`, `credential_use`, `open_world` | update, publish, schedule, unschedule, delete | Remote changes with destructive, public, or hard-to-reverse effects. |

The server uses argument-bound execution tokens as a second-call gate. **CURRENT**
MCP tool consent remains a host responsibility; the token itself cannot establish
human approval. Unknown clients that automatically approve calls are not an
approved safety boundary.

## Egress and resilience

The configured URL is checked for HTTPS URL syntax. The locked upstream client
authenticates to Substack and requires the hostname to match a publication in
the authenticated account before it changes its publication endpoint. It
supports account-owned custom domains. This is an upstream-client contract and
is tested locally with synthetic requests. The MCP server does not accept a URL
as a tool argument. Its session validates every Requests send, including
redirect hops: only HTTPS on port 443 (or the default port) to Substack or a
configured publication host matched against the authenticated profile is
permitted. Synthetic tests cover off-host redirects, HTTP downgrade redirects,
nonstandard ports, and unrelated account publication hosts.

The upstream session has bounded connect/read timeouts and finite GET retry
behavior. Mutating methods are not automatically retried. Sync handlers run via
the SDK's worker-thread behavior; an interrupted client request may not stop an
already-running upstream call, so timeout bounds remain the backstop.

## Unresolved decisions

- Actual MCP host products and versions are not named; host consent behavior is
  therefore unverified.
- Custom-domain support is based on the authenticated account publication
  response and the locked upstream client behavior. Live endpoint behavior is
  not qualified in routine tests.
- The implementation has no independent per-user authorization or tenant
  separation. It is intended for one local operator/account per server process.
