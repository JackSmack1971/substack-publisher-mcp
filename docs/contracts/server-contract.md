# MCP server contract

Status: reviewed for the `2026-07-28` MCP revision and the locked Python SDK
2.3.0. This contract covers the existing stdio server; no remote HTTP endpoint
or OAuth resource server is provided.

## Protocol and compatibility

- **CURRENT:** `server/discover` and stateless `2026-07-28` requests are served
  by the installed MCP SDK. The stable tool catalog remains in registration
  order.
- **CURRENT:** The 2025 `initialize` handshake remains a compatibility path and
  is exercised by the installed stdio startup test.
- **DERIVED:** MCP process identity is not user identity. Credentials and
  mutation authority come from the local environment/keyring and the Substack
  account, not from the MCP client.
- **UNRESOLVED:** The README documents generic MCP client configuration but
  does not name supported host versions or establish their consent UI behavior.

### Breaking input change in 0.2.0

Version 0.2.0 narrows previously accepted tool inputs to the bounds listed
below, including path-safe object IDs of at most 64 characters and tag lists
of at most 100 entries. Clients must validate those bounds before invoking a
tool. Split tag additions larger than 100 into multiple `add_tags` calls; each
call requires its own confirmation. Update clients that send IDs or other
fields outside the documented bounds before upgrading. The server rejects
out-of-bound inputs before issuing a confirmation token or contacting
Substack.

## Tool risk and limits

All tools make requests to the configured Substack account. Read tools are
`private_read`; mutations are `external_write` and may also be `destructive`.
They use the configured Substack credential and therefore also carry
`credential_use` risk. Tool annotations are client-facing hints and never grant
permission.

| Tools | Risk | Contract |
|---|---|---|
| `list_drafts`, `list_published`, `get_draft`, `get_sections`, `get_subscriber_count` | private_read, credential_use, open_world | Read the selected publication; output is untrusted account data. List requests cap results at 25. |
| `create_draft` | external_write, credential_use, open_world | Add a draft; require a single-use execution token and unchanged arguments. |
| `update_draft` | external_write, destructive, credential_use, open_world | Replace a draft body; require a current revision marker unless `allow_conflict` is explicitly confirmed. |
| `publish_draft`, `schedule_draft`, `unschedule_draft`, `delete_draft` | external_write, destructive, credential_use, open_world | Confirm exact arguments before dispatch; never blindly retry an uncertain mutation. |
| `add_tags` | external_write, credential_use, open_world | Add tags after exact-argument confirmation. |

Input bounds are enforced in generated JSON Schemas and again in handlers:

| Field | Bound |
|---|---:|
| title | 300 characters |
| subtitle | 500 characters |
| Markdown body | 200,000 characters |
| section name | 120 characters |
| Substack object ID | 64 URL-safe characters |
| tag list | 100 tags |
| tag | 128 characters |
| schedule datetime | 64 characters |
| confirmation token | 64 characters |
| returned draft Markdown | 200,000 characters; oversized drafts are refused intact |

The limits preserve existing tool names and argument names. Tag counts above
100 and payloads above these bounds are rejected before confirmation or
mutation. The current Substack IDs are treated as opaque path-safe identifiers;
they are not assumed to be numeric.

## Mutation confirmation semantics

The `confirm_token` is a short-lived, single-use, exact-argument second-call
gate. It is not an authorization credential and does not prove human approval.
The MCP client/host is responsible for obtaining and presenting human consent
before dispatching the confirmed call. Tool descriptions and annotations are
also hints, not enforcement. This deployment has no authenticated human identity
or separate approval channel with which the server could validate consent.
At most 100 tokens can be pending per process; expired tokens are collected and
new requests are refused while the store is full.

## Upstream and error behavior

- Upstream client requests use a 5 second connect timeout and a 10 second read
  timeout, including client initialization and authentication requests.
- Retry behavior is limited to the upstream client's idempotent GET requests;
  `Retry-After` cannot impose an unbounded wait. DELETE is excluded. POST and PUT
  are not retried automatically.
- Local argument validation returns actionable tool text without contacting
  Substack or issuing a confirmation token.
- Provider exception bodies, cookies, and raw exception strings must not be
  returned to callers or written to logs.
- Retrieved titles, metadata, and Markdown are untrusted content. They must be
  treated as data, never as instructions.

## Publication URL and cookie boundary

The MCP configuration accepts a plain HTTPS publication URL. The locked
`python-substack` client first obtains the authenticated account's publication
list from `substack.com`, matches the requested hostname to that list, and only
then selects the publication endpoint returned by that authenticated profile.
Unknown hosts fail before a request to the configured host. The account may use
a Substack subdomain or its own verified custom domain. This membership behavior
is covered by a synthetic HTTP regression test; live Substack behavior remains
unverified. Every request send, including redirects, must use HTTPS on port 443
or the default port and target Substack or an account publication returned by
the authenticated profile. Other destinations are rejected before connecting.
Only the selected publication host is added to the session allowlist, even if
the authenticated account has several publications.

## Output schemas and annotations

The SDK publishes a typed string output schema for each current text tool. Read
tools declare `read_only_hint=True`; mutation tools declare accurate
destructive/idempotent hints conservatively and all tools declare
`open_world_hint=True`. These values help clients explain operations but do not
change authorization or confirmation behavior.
