# Substack Publisher MCP

An unofficial MCP server for Substack — create, edit, and publish posts from Claude
(or any MCP client), with real Markdown formatting and server-enforced confirmation
on destructive actions.

**Unofficial.** Substack has no public API. This wraps
[`python-substack`](https://github.com/ma2za/python-substack), which uses
Substack's internal web endpoints. It can break if Substack changes them.

## Why this exists

[`substack-mcp-plus`](https://github.com/ty13r/substack-mcp-plus) is an existing,
more widely-used server that does something similar. This project started as an
analysis of that codebase. Two things stood out:

1. **Its headline bug — bold/italic/lists/blockquotes rendering as literal
   `**markdown**` syntax instead of formatted text — is a bug in its own
   converter, not a Substack limitation.** Its hand-rolled `MarkdownConverter`
   builds correct structured blocks, but `post_handler.py`'s
   `_add_blocks_to_post()` only passes that structure through for plain
   paragraphs. Headings, list items, and blockquotes are instead flattened
   through `_extract_text_from_content()`, which re-serializes bold/italic
   marks back into `**text**` strings and hands the API plain text — so
   Substack renders exactly what it's given: the markdown syntax, literally.

2. **The `python-substack` library it depends on has moved on.** The project's
   docs describe it as unmaintained for 2+ years and blame it for missing
   scheduling and broken images. The current release (0.10.0) has a
   CommonMark-based renderer (`markdown-it-py`, not regex), one-call
   `create_draft_from_markdown` / `update_draft_from_markdown` methods that
   handle images/tags/scheduling/publishing, **optimistic-concurrency conflict
   detection** on updates, and working `schedule_draft`/`unschedule_draft`.
   None of this is used — the project still maintains ~2,500 lines of its own
   block-building and content-extraction logic that duplicates (and in the
   cases above, breaks) what the library now does natively.

This server is a thin layer over that modern API surface instead of a parallel
reimplementation, plus one structural fix described below.

## What's different, concretely

| | substack-mcp-plus | this project |
|---|---|---|
| Markdown parsing | Hand-rolled regex converter | `markdown-it-py` (CommonMark) via the library |
| Bold/italic in headings, lists, blockquotes | Broken (shows as `**text**`) | Correct |
| Concurrent-edit safety | None — last write wins | Revision marker + conflict detection; refuses stale updates unless you opt in |
| Scheduling | Removed ("404 errors") | Works (current library targets the live endpoint) |
| Tags | Not supported | Supported |
| Confirmation on publish/delete | A sentence in the tool description asking the calling model to wait — nothing stops it from skipping that | Server-issued, single-use token; the second call is rejected without a token the server itself generated |
| Auth | Browser automation + CAPTCHA solving + dual keyring/file storage paths | Paste one cookie string, stored once |
| Tool count | 12, several duplicating library logic | 11, each a thin pass-through |

### The confirmation design specifically

substack-mcp-plus's safety mechanism is entirely in the tool's natural-language
description: *"you MUST ask the user to confirm... never set confirm=true on
the first request."* That's an instruction to the calling model, not a
constraint the server enforces — a model that ignores it, or is manipulated by
injected content into ignoring it, hits publish or delete with nothing to
stop it.

Here, the first call to a destructive tool (`create_draft`, `update_draft`,
`publish_draft`, `schedule_draft`, `delete_draft`) always returns a summary and
a random server-generated `confirm_token` — it never acts. The action only
executes if a second call supplies that exact token, scoped to that tool and
those arguments, and it expires in 5 minutes. A model can still choose not to
show the user the summary, but it cannot *act* without a token only the server
can produce.

## Setup

```bash
pip install -e .
substack-publisher-mcp-setup
```

The setup script asks for your publication URL and a cookie string (copied
from your browser's dev tools while logged into Substack), verifies it, and
stores it via your OS keychain (`keyring`).

Then add to your MCP client config:

```json
{
  "mcpServers": {
    "substack-publisher-mcp": {
      "command": "substack-publisher-mcp",
      "env": { "SUBSTACK_PUBLICATION_URL": "https://yourpub.substack.com" }
    }
  }
}
```

## Tools

- `list_drafts`, `list_published` — browse posts
- `get_draft` — fetch a draft as Markdown with an embedded revision marker
- `create_draft` — new draft from Markdown (title, subtitle, audience, tags, section)
- `update_draft` — replace a draft's content; detects and refuses stale overwrites
- `publish_draft` — publish immediately (confirmation required)
- `schedule_draft` / `unschedule_draft` — schedule or cancel a future publish
- `delete_draft` — permanent delete (confirmation required)
- `add_tags`, `get_sections`, `get_subscriber_count`

## Known limitations

- Still unofficial, still reverse-engineered, still can break without notice.
- Embeds, polls, and other block types with no Markdown equivalent are
  preserved on round-trip but can't be authored through this tool.
- No image upload tool yet (the library supports it via `get_image()`; not
  wired up to an MCP tool in this first pass).

## License

MIT.
