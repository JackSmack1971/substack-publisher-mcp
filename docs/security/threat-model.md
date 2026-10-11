# Threat model

Status: **CURRENT** for the local stdio deployment. **DERIVED** statements are
based on the locked MCP and `python-substack` SDK behavior. No live credentials
or Substack requests are used by this review.

## Trust boundaries and protected data

- The MCP host and its user interface control tool invocation and human consent.
- Tool arguments and tool-returned Substack titles, section names, and draft
  content are untrusted data.
- The local environment, OS keyring, and authenticated Substack account are
  credential and authority boundaries.
- Protected data includes the Substack session cookie, account credentials,
  private draft content, and publication metadata.

## Threats and controls

| Threat | Control | Residual risk |
|---|---|---|
| Malicious/oversized tool input | Bounded JSON Schemas and duplicate server-side checks; no confirmation token on rejected input. | Request JSON is parsed by the MCP SDK before handler validation. |
| Stalled upstream or excessive retry wait | Finite connect/read timeouts; bounded GET retries without unbounded `Retry-After`; no retries for mutation methods. | A blocking library call may continue in its worker thread until its timeout. |
| Cookie sent to an arbitrary configured hostname or redirect target | Production client sends the cookie only to HTTPS Substack profile endpoints until that authenticated profile attests publication hosts; it rejects non-HTTPS URLs, nonstandard ports, and hosts outside that allowlist on every send/redirect hop. The locked client also matches the requested host against the profile. | Trusts authenticated Substack publication metadata and the locked client's host matching. Live redirect behavior is not proven offline. |
| Model prompt injection in retrieved content | Treat all upstream text as data; tool instructions explicitly warn against following content as authority. Host must mediate mutation consent. | A model can still be influenced by content; only a trusted host/user consent UI can authorize execution. |
| Replay or argument substitution | Random, expiring, single-use tokens bind tool and exact arguments; token consumed on failed confirmation attempt. | Tokens are process-wide and not caller-bound; this is not human or user authorization. |
| Memory growth from unconfirmed calls | Pending-action store is capped at 100 entries; expiry garbage collection runs on creation. | A local caller can fill the cap and deny new pending actions until tokens expire or the process restarts. |
| Secret leakage in errors/logs | Report exception types and stable messages; never log provider exception bodies in normal tool paths or top-level runtime failure. | Third-party/library logs outside this server's logger remain dependent on their configuration. |
| Concurrent/independent local callers share account authority | Documented one-operator/one-account-per-process deployment model. | No per-user scopes or tenant isolation exist. Do not share one process across mutually untrusted operators. |

## HTTP/OAuth scope

There is no remote MCP HTTP listener, bearer-token validator, OAuth metadata,
or MCP-to-upstream token passthrough. The server uses its own configured
Substack credentials only. OAuth issuer/audience/scope checks are not applicable
to this stdio deployment; adding remote transport requires a separate threat
model and authorization design.
