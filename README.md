# Substack Publisher MCP

An unofficial MCP server for authors who want to create, edit, and publish
Substack posts from an MCP client using Markdown and a two-step confirmation flow.
It wraps `python-substack`, which uses Substack's internal endpoints and can break
when those endpoints change. This project is not affiliated with Substack Inc.

## Quickstart

Prerequisites: Python 3.10+ and [uv](https://docs.astral.sh/uv/), plus a logged-in
Substack browser session and a working OS keyring for interactive credential setup.
From a checkout of this repository, install the locked environment, run tests to verify startup, then configure credentials:

```sh
uv sync --locked --extra dev --python 3.11
uv run --locked --extra dev pytest -q
uv run --locked substack-publisher-mcp-setup
```

The tests include an offline startup smoke test: the installed CLI starts over
stdio, completes MCP initialization, and advertises all 12 tools. Tests do not
need credentials and do not publish or contact Substack. They do not establish
that Substack's live endpoints currently work.

For this publication, setup asks for `https://brettousignant.substack.com` and
the value of one browser cookie. In Chrome or Edge, sign in to that publication,
press **F12**, open **Application** (Chrome) or **Storage** (Edge), then
**Cookies**. Select the Substack domain (`https://substack.com` or the matching
Substack domain shown) and find the row named `substack.sid`. Copy only its
**Value** cell. Run `uv run --locked substack-publisher-mcp-setup` and paste
that value at the hidden prompt; setup adds `substack.sid=` automatically. You
can also enter `substack.sid=VALUE`. Do not copy a `Cookie:` header. Setup checks
the HTTPS publication URL and cookie format locally, then validates the session
with the restricted read-only requests before saving it to the OS keyring. It
never reads browser cookie files or prints the value. Validation contacts
Substack.

To separately inspect a redirect without saving credentials, run
`uv run --locked substack-publisher-mcp-setup --diagnose-redirect` locally.
This mode requires the exact publication URL above and a freshly supplied
hidden cookie value. It makes only the client's two constructor GET requests,
blocks redirects, prints only response statuses and a safely parsed redirect
host/path, then closes the session. It does not read environment or keyring
credentials, persist the cookie, or authorize any further live requests.

Add this entry to your MCP client's configuration, replacing the path and URL:

```json
{
  "mcpServers": {
    "substack-publisher-mcp": {
      "command": "/absolute/path/to/checkout/.venv/bin/substack-publisher-mcp",
      "env": { "SUBSTACK_PUBLICATION_URL": "https://yourpub.substack.com" }
    }
  }
}
```

On Windows, use the absolute path to `.venv\\Scripts\\substack-publisher-mcp.exe`
with JSON-escaped backslashes. Use the same publication URL as during setup,
including any trailing slash: keyring entries are keyed by the exact URL string.
The client launches the stdio server; running the CLI manually waits for MCP input.

## Features and usage

| Tool | Behavior |
|---|---|
| `list_drafts`, `list_published` | List recent posts; limit capped at 25 |
| `get_draft` | Export Markdown with revision and preservation markers |
| `get_sections`, `get_subscriber_count` | Read publication metadata |
| `create_draft` | Create from Markdown with title, subtitle, audience, tags, and optional section name |
| `update_draft` | Replace the body, optionally change subtitle, with revision conflict checks |
| `publish_draft` | Publish now; `send` defaults to true, `share_automatically` to false |
| `schedule_draft` | Schedule using an ISO 8601 datetime; include a timezone offset |
| `unschedule_draft` | Cancel a scheduled publish |
| `delete_draft` | Permanently delete a draft |
| `add_tags` | Add tags to a draft or published post |

Markdown rendering is delegated to the library's CommonMark renderer, with
extensions such as footnotes and strikethrough. This server does not implement a
separate Markdown converter.

### Confirmation

Every remote mutation—`create_draft`, `update_draft`, `publish_draft`,
`schedule_draft`, `unschedule_draft`, `delete_draft`, and `add_tags`—requires two
calls. First omit `confirm_token`, show the returned summary to the
user, and obtain explicit approval. Then repeat the same arguments with the
returned token. Tokens are scoped to the tool and arguments, single-use, expire
after five minutes, and live only in this server process. Restarting loses them.
A failed validation or API attempt consumes the token; start again for a retry.

This enforces a second call with a valid token. It cannot prove that a client
actually asked a human for approval; the caller can request and return the token
without human consent.

### Editing safely

Call `get_draft`, edit the Markdown body, and pass the returned Markdown portion
(including revision and preservation markers, excluding the explanatory heading)
to `update_draft`. Updates with missing or stale revision metadata are refused by
the library unless `allow_conflict=true`. An override changes the confirmed
arguments and requires a fresh confirmation flow. It does not bypass other
library safeguards.

Unsupported blocks such as embeds and polls are represented by preservation
markers. Keep those markers unchanged; the library refuses missing or altered
unsupported blocks. The tool does not expose `allow_unsupported_change` or
`allow_image_replacement`. There is no standalone image upload tool, and these
unsupported blocks cannot be authored through this server.

## Configuration

Authentication resolves in the order below; use cookie authentication when
possible. No credentials are needed just to start the server or list its tools.

| Variable or storage | Required/default | Purpose |
|---|---|---|
| `SUBSTACK_PUBLICATION_URL` | Required when calling Substack tools | Full publication URL |
| `SUBSTACK_COOKIES_STRING` | Optional, first preference | Cookie header string; bypasses keyring lookup |
| OS keyring | Optional, second preference | Cookie saved by setup for the exact publication URL |
| `SUBSTACK_EMAIL`, `SUBSTACK_PASSWORD` | Optional, final fallback | Both required; login may encounter CAPTCHA |

## Architecture and directory structure

The MCP client calls `server.py`, which validates confirmation through `pending.py`
and forwards operations to `python-substack`. `auth.py` creates the client using
environment credentials or the OS keyring. `setup_auth.py` verifies and stores
credentials interactively. Authentication is lazy, on the first tool call.
Setup validates the cookie through the same bounded, GET-only restricted session
used by live qualification, and writes to the exact publication URL key only
after the authenticated profile request succeeds.

## Stage 3 live compatibility qualification

Live compatibility is **UNVERIFIED** until an operator explicitly runs the
qualification command and receives a `SUPPORTED` report. The verifier is
separate from the MCP tools and supports cookie authentication only. It never
uses email/password login because the locked client logs in with POST.

The default run checks authenticated client initialization (including the
client's implicit profile and publication sign-in GETs), publication sections,
one draft-list result, and one published-post-list result. Subscriber counts
are not requested. To include one draft Markdown retrieval, pass that draft's
numeric identifier of at most 20 digits explicitly with `--draft-id`; the report
never includes article or draft content.

First ensure the existing cookie is available through `SUBSTACK_COOKIES_STRING`
or the OS keyring entry created by `substack-publisher-mcp-setup`. Then review
the target publication and explicitly authorize external requests:

```sh
substack-publisher-mcp-qualify --publication-url https://your-publication.substack.com --live
```

Optional single-draft retrieval:

```sh
substack-publisher-mcp-qualify --publication-url https://your-publication.substack.com --live --draft-id 123456
```

Without `--live`, no credential lookup, client construction, or network request
occurs. The command accepts only HTTPS to the configured publication host and
the two `substack.com` initialization endpoints. It allows GET on the exact
qualification routes, rejects every other method and destination, does not
follow redirects, disables retries, uses bounded timeouts, and limits the run
to six requests. Email/password credentials are not used. This boundary is
independent of MCP tool descriptions.

The JSON report includes timestamp, runtime/library versions, publication host,
operation names, HTTP statuses where available, response shape counts, a
result classification, and limitations. It excludes cookies, response bodies,
article text, private draft content, subscriber identities, and provider
payloads. Save or share the report only after reviewing it for local policy;
never attach cookie values or raw HTTP traces. A timeout or 429 is inconclusive
and is classified as `TRANSIENT_ERROR` or `RATE_LIMITED`, not unsupported.
Other result classes are `AUTH_FAILURE`, `PERMISSION_DENIED`, `ENDPOINT_DRIFT`,
`RESPONSE_CONTRACT_MISMATCH`, and `BLOCKED`. Qualification proves compatibility
only for the approved account, publication, endpoints, and time of execution.

The command is intentionally not run as part of development or CI. Offline
tests exercise the boundary with synthetic requests and do not establish live
Substack compatibility.

```text
src/substack_publisher_mcp/  Server, confirmation store, authentication, setup CLI
tests/                      Confirmation, mocked tools, real stdio startup
pyproject.toml              Package metadata, dependency ranges, CLI entry points
uv.lock                     Exact resolved dependency baseline, including dev tools
```

## Developer command center

| Command | Purpose |
|---|---|
| `uv sync --locked --extra dev --python 3.11` | Create/sync the baseline development environment |
| `uv run --locked --extra dev pytest -q` | Run the complete offline suite |
| `uv run --locked --extra dev pytest -q tests/test_startup.py` | Check the installed entry point and MCP handshake |
| `uv run --locked substack-publisher-mcp` | Run the stdio server |
| `uv run --locked substack-publisher-mcp-setup` | Verify and save browser credentials |
| `uv run --locked substack-publisher-mcp-qualify` | Print a no-network report unless explicitly given `--live` |
| `uv lock --upgrade` | Intentionally update resolved dependencies; review the lock diff and rerun tests |

## Stack inventory

The baseline uses Python 3.11, MCP 2.3.0, python-substack 0.10.0, and keyring
25.7.0; all transitive and development versions are in `uv.lock`. The manifest
allows Python 3.10+ and MCP 2.x starting at 2.3.0. Use `--locked` to reject stale
lockfiles. Editable pip installs follow manifest ranges and do not reproduce the
lockfile.

## Reproducibility & maintenance

For a fresh clone, install and verify the locked baseline:

```sh
uv sync --locked --extra dev --python 3.11
uv run --locked --extra dev pytest -q
```

When updating dependencies, review the lock diff and rerun the suite. Verify the
minimum Python with `uv run --isolated --python 3.10 --locked --extra dev pytest -q`.
Recreate local environments with `uv sync`; confirmation state clears
when the server exits, while stored keyring credentials persist.

## Testing & verification

Confirmation tests call the production function with a fresh store per test.
Mocked tool tests verify no mutation before confirmation, exact argument
forwarding, replay/mismatch rejection, and conflict override confirmation.
The stdio smoke test checks the actual installed CLI and dependency integration.
CI is configured to run the locked test suite on Python 3.11 using GitHub-hosted
Linux and Windows runners. GitHub runner execution and current live Substack
compatibility remain unverified in this release-candidate check. There is no lint
or typecheck command in this repository.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| MCP client cannot find the command | Virtual environment executable is absent or path is wrong | Run `uv sync --locked --extra dev --python 3.11` and configure its absolute CLI path |
| Publication URL error | Missing or incomplete URL | Set `SUBSTACK_PUBLICATION_URL` to the full `https://…` URL |
| No credentials or keyring backend error | Setup not run, different URL, or no OS keyring backend | Run setup with the exact configured URL; alternatively supply `SUBSTACK_COOKIES_STRING` |
| Setup reports `AUTH_FAILURE` (HTTP 401) | The profile endpoint rejected the session | Confirm the response is from the profile check before replacing the cookie |
| Setup reports `REDIRECT` | The restricted check received a redirect; authentication is inconclusive | Review the sanitized host, path, and status. Setup does not follow the redirect or save the cookie; do not infer expiration from the redirect alone |
| Setup cannot reach Substack | Network timeout, firewall, VPN, or connectivity issue | Check the connection and retry; setup does not retry or bypass its restricted request boundary |
| Setup reports keyring save failure | Windows keyring backend is unavailable | Enable a Windows Credential Manager keyring and rerun setup |
| Conflict on update | Missing revision marker or draft changed remotely | Fetch with `get_draft`, retain markers, reapply edits, and obtain a fresh confirmation |
| Invalid confirmation token | Expired, consumed, or server restarted | Call again without the token and request confirmation again |

## Contributing and project status

Contribution guidelines are not yet formalized. Open an issue before large
changes and include test output with changes. Security reporting and a code of
conduct are not yet documented. No roadmap is committed here.

## License

[MIT](LICENSE).
