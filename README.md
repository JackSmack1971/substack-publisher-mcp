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

Setup asks for your publication URL and a Cookie request header value copied
from an authenticated request in browser DevTools → Network. A single cookie
must include its name: `substack.sid=COOKIE_VALUE`, rather than the bare value.
Input is hidden. Setup verifies the credentials before storing them in the OS
keyring; this step contacts Substack.

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
There is no CI workflow, lint command, or typecheck command in this repository.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| MCP client cannot find the command | Virtual environment executable is absent or path is wrong | Run `uv sync --locked --extra dev --python 3.11` and configure its absolute CLI path |
| Publication URL error | Missing or incomplete URL | Set `SUBSTACK_PUBLICATION_URL` to the full `https://…` URL |
| No credentials or keyring backend error | Setup not run, different URL, or no OS keyring backend | Run setup with the exact configured URL; alternatively supply `SUBSTACK_COOKIES_STRING` |
| CAPTCHA during login | Email/password authentication challenged | Use a Cookie header from an authenticated browser session |
| Conflict on update | Missing revision marker or draft changed remotely | Fetch with `get_draft`, retain markers, reapply edits, and obtain a fresh confirmation |
| Invalid confirmation token | Expired, consumed, or server restarted | Call again without the token and request confirmation again |

## Contributing and project status

Contribution guidelines are not yet formalized. Open an issue before large
changes and include test output with changes. Security reporting and a code of
conduct are not yet documented. No roadmap is committed here.

## License

[MIT](LICENSE).
