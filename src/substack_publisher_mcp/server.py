# ABOUTME: MCP server for Substack, built directly on python-substack's
# ABOUTME: markdown-native API (CommonMark via markdown-it-py) instead of a
# ABOUTME: hand-rolled converter, with server-enforced confirmation for
# ABOUTME: destructive actions.

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer
from substack import Api as SubstackApi
from substack.exceptions import DraftConflictError, SubstackAPIException

from substack_publisher_mcp.auth import AuthError, build_client
from substack_publisher_mcp.pending import require_confirmation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="substack-publisher-mcp",
    version="0.1.0",
    instructions=(
        "Tools for creating, editing, and publishing Substack posts. Content is "
        "authored in Markdown (CommonMark), which renders as real formatting on "
        "Substack, not literal markdown syntax. Destructive actions "
        "(create_draft, update_draft, publish_draft, schedule_draft, delete_draft) "
        "require a two-step confirmation: call once without confirm_token to see "
        "a summary and receive a token, show that summary to the user, then call "
        "again with the token only after the user confirms."
    ),
)

_PUBLICATION_URL: Optional[str] = None
_client: Optional[SubstackApi] = None


def _publication_url() -> str:
    global _PUBLICATION_URL
    if _PUBLICATION_URL is None:
        url = os.getenv("SUBSTACK_PUBLICATION_URL")
        if not url:
            raise ValueError("SUBSTACK_PUBLICATION_URL must be set")
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "SUBSTACK_PUBLICATION_URL must be a full URL, e.g. https://you.substack.com"
            )
        _PUBLICATION_URL = url
    return _PUBLICATION_URL


def _client_or_raise() -> SubstackApi:
    global _client
    if _client is None:
        try:
            _client = build_client(_publication_url())
        except AuthError as e:
            raise RuntimeError(f"Authentication error: {e}") from e
    return _client


def _friendly(prefix: str, e: Exception) -> str:
    logger.error(f"{prefix}: {e}")
    if isinstance(e, DraftConflictError):
        return (
            f"Conflict: {e}\n\nThe draft changed on Substack since it was last fetched. "
            "Call get_draft again to get the current content and revision marker, "
            "reapply your edits, and try again — or pass allow_conflict=true to "
            "overwrite anyway."
        )
    if isinstance(e, SubstackAPIException):
        return f"Substack API error: {e}"
    return f"Error: {prefix}: {e}"


# ---- read-only tools -------------------------------------------------


@mcp.tool(description="List recent draft posts (title, id, last updated). Max limit 25.")
def list_drafts(limit: int = 10) -> str:
    client = _client_or_raise()
    try:
        drafts = client.get_drafts(filter="draft", limit=min(limit, 25))
        lines = [
            f"- {d.get('draft_title') or d.get('title') or 'Untitled'} (ID: {d.get('id')})"
            for d in drafts
        ]
        return f"Found {len(lines)} drafts:\n" + "\n".join(lines) if lines else "No drafts found."
    except Exception as e:
        return _friendly("failed to list drafts", e)


@mcp.tool(description="List recently published posts (title, id, publish date). Max limit 25.")
def list_published(limit: int = 10) -> str:
    client = _client_or_raise()
    try:
        posts = client.get_published_posts(limit=min(limit, 25))
        items = posts.get("posts", posts) if isinstance(posts, dict) else posts
        lines = [
            f"- {p.get('title', 'Untitled')} (ID: {p.get('id')}, Published: {p.get('post_date', 'unknown')})"
            for p in items
        ]
        return f"Found {len(lines)} published posts:\n" + "\n".join(lines) if lines else "No published posts found."
    except Exception as e:
        return _friendly("failed to list published posts", e)


@mcp.tool(
    description=(
        "Fetch a draft's content as Markdown, with an embedded revision marker. "
        "To safely edit a draft, fetch it with this tool first and pass the "
        "returned markdown (marker included) to update_draft — the server will "
        "refuse the update if the draft changed elsewhere in the meantime."
    )
)
def get_draft(draft_id: str) -> str:
    client = _client_or_raise()
    try:
        result = client.export_draft_to_markdown(draft_id)
        draft = result["draft"]
        title = draft.get("draft_title") or draft.get("title") or "Untitled"
        subtitle = draft.get("draft_subtitle") or draft.get("subtitle") or ""
        text = (
            f"Title: {title}\nSubtitle: {subtitle}\nID: {draft.get('id')}\n\n"
            f"--- Markdown (includes revision marker; pass back unchanged apart "
            f"from your edits when calling update_draft) ---\n\n{result['markdown']}"
        )
        if result.get("unsupported_nodes"):
            text += (
                f"\n\n[Note: {len(result['unsupported_nodes'])} block(s) in this draft use "
                "Substack features with no Markdown equivalent (e.g. embeds, polls) and were "
                "preserved as-is but aren't shown above. update_draft will keep them intact "
                "unless you pass allow_unsupported_change.]"
            )
        return text
    except Exception as e:
        return _friendly(f"failed to fetch draft {draft_id}", e)


@mcp.tool(description="List the publication's sections/categories with their ids.")
def get_sections() -> str:
    client = _client_or_raise()
    try:
        sections = client.get_sections() or []
        lines = [f"- {s.get('name', 'Unnamed')} (ID: {s.get('id')})" for s in sections]
        return "Sections:\n" + "\n".join(lines) if lines else "No sections found."
    except Exception as e:
        return _friendly("failed to list sections", e)


@mcp.tool(description="Get the total subscriber count for the publication.")
def get_subscriber_count() -> str:
    client = _client_or_raise()
    try:
        count = client.get_publication_subscriber_count()
        return f"Subscribers: {count:,}"
    except Exception as e:
        return _friendly("failed to get subscriber count", e)


def _resolve_section_id(client: SubstackApi, section_name: str) -> int:
    sections = client.get_sections() or []
    for s in sections:
        if s.get("name", "").strip().lower() == section_name.strip().lower():
            return s.get("id")
    raise ValueError(f"No section named '{section_name}' found. Call get_sections to see valid names.")


# ---- confirmation-gated tools -------------------------------------------------


@mcp.tool(
    description=(
        "Create a new draft post from Markdown (CommonMark: headers, **bold**, "
        "*italic*, links, images, lists, blockquotes, code fences, footnotes, "
        "~~strikethrough~~ all render as real formatting, not literal markdown "
        "syntax). IMPORTANT: this is a content-creating action — you MUST show the "
        "user the confirmation summary and get their explicit go-ahead before "
        "calling again with confirm_token, even if they already asked you to "
        "create the post. Leave confirm_token empty on the first call."
    )
)
def create_draft(
    title: str,
    markdown: str,
    subtitle: str = "",
    audience: str = "everyone",
    tags: Optional[List[str]] = None,
    section_name: Optional[str] = None,
    confirm_token: Optional[str] = None,
) -> str:
    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {
        "title": title,
        "markdown": markdown,
        "subtitle": subtitle,
        "audience": audience,
        "tags": tags,
        "section_name": section_name,
    }

    def summarize() -> str:
        preview = markdown[:200] + ("..." if len(markdown) > 200 else "")
        return (
            f"Create a new draft:\n"
            f'- Title: "{title}"\n'
            f'- Subtitle: "{subtitle or "[none]"}"\n'
            f"- Audience: {audience}\n"
            f"- Content preview: {preview}"
        )

    confirmed, message = require_confirmation("create_draft", confirm_args, confirm_token, summarize)
    if not confirmed:
        return message

    try:
        section_id = _resolve_section_id(client, section_name) if section_name else None
        result = client.create_draft_from_markdown(
            title=title,
            markdown=markdown,
            subtitle=subtitle,
            audience=audience,
            tags=tags,
            draft_section_id=section_id,
        )
        draft = result["draft"]
        return f"Draft created.\nID: {draft.get('id')}\nTitle: {title}"
    except Exception as e:
        return _friendly("failed to create draft", e)


@mcp.tool(
    description=(
        "Replace a draft's content from Markdown. WARNING: this replaces the "
        "entire body. For a safe edit: call get_draft first, edit the returned "
        "markdown, and pass it back here unchanged apart from your edits — the "
        "embedded revision marker lets the server detect and refuse conflicting "
        "concurrent changes. Requires confirmation (leave confirm_token empty on "
        "the first call)."
    )
)
def update_draft(
    draft_id: str,
    markdown: str,
    subtitle: Optional[str] = None,
    allow_conflict: bool = False,
    confirm_token: Optional[str] = None,
) -> str:
    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {
        "draft_id": draft_id,
        "markdown": markdown,
        "subtitle": subtitle,
        "allow_conflict": allow_conflict,
    }

    def summarize() -> str:
        base = f"Replace the content of draft {draft_id} with new markdown ({len(markdown)} characters)."
        if subtitle is not None:
            base += " Subtitle will also change."
        return base

    confirmed, message = require_confirmation("update_draft", confirm_args, confirm_token, summarize)
    if not confirmed:
        return message

    try:
        client.update_draft_from_markdown(
            draft_id=draft_id,
            markdown=markdown,
            subtitle=subtitle,
            allow_conflict=allow_conflict,
        )
        return f"Draft {draft_id} updated."
    except Exception as e:
        return _friendly(f"failed to update draft {draft_id}", e)


@mcp.tool(
    description=(
        "Publish a draft immediately, emailing all subscribers unless send=false. "
        "Cannot be undone. Requires confirmation (leave confirm_token empty on "
        "the first call)."
    )
)
def publish_draft(
    draft_id: str,
    send: bool = True,
    share_automatically: bool = False,
    confirm_token: Optional[str] = None,
) -> str:
    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {
        "draft_id": draft_id,
        "send": send,
        "share_automatically": share_automatically,
    }

    def summarize() -> str:
        try:
            draft = client.get_draft(draft_id)
            title = draft.get("draft_title") or draft.get("title") or "Untitled"
        except Exception:
            title = "(unable to fetch title)"
        email_note = "This will email all subscribers." if send else "Email sending is OFF for this publish."
        return f'Publish draft "{title}" (ID: {draft_id}) now.\n{email_note}\nThis cannot be undone.'

    confirmed, message = require_confirmation("publish_draft", confirm_args, confirm_token, summarize)
    if not confirmed:
        return message

    try:
        client.publish_draft(draft_id, send=send, share_automatically=share_automatically)
        return f"Draft {draft_id} published."
    except Exception as e:
        return _friendly(f"failed to publish draft {draft_id}", e)


@mcp.tool(
    description=(
        "Schedule a draft to publish automatically at a future time (ISO 8601 "
        "datetime). Requires confirmation (leave confirm_token empty on the "
        "first call)."
    )
)
def schedule_draft(draft_id: str, publish_at_iso: str, confirm_token: Optional[str] = None) -> str:
    from datetime import datetime

    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {"draft_id": draft_id, "publish_at_iso": publish_at_iso}

    confirmed, message = require_confirmation(
        "schedule_draft",
        confirm_args,
        confirm_token,
        lambda: f"Schedule draft {draft_id} to publish at {publish_at_iso}.",
    )
    if not confirmed:
        return message

    try:
        dt = datetime.fromisoformat(publish_at_iso)
        client.schedule_draft(draft_id, dt)
        return f"Draft {draft_id} scheduled for {publish_at_iso}."
    except Exception as e:
        return _friendly(f"failed to schedule draft {draft_id}", e)


@mcp.tool(description="Cancel a pending scheduled publish for a draft.")
def unschedule_draft(draft_id: str) -> str:
    client = _client_or_raise()
    try:
        client.unschedule_draft(draft_id)
        return f"Schedule cancelled for draft {draft_id}."
    except Exception as e:
        return _friendly(f"failed to unschedule draft {draft_id}", e)


@mcp.tool(
    description=(
        "Permanently delete a draft. Cannot be undone. Requires confirmation "
        "(leave confirm_token empty on the first call)."
    )
)
def delete_draft(draft_id: str, confirm_token: Optional[str] = None) -> str:
    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {"draft_id": draft_id}

    def summarize() -> str:
        try:
            draft = client.get_draft(draft_id)
            title = draft.get("draft_title") or draft.get("title") or "Untitled"
        except Exception:
            title = "(unable to fetch title)"
        return f'Permanently delete draft "{title}" (ID: {draft_id}). This cannot be undone.'

    confirmed, message = require_confirmation("delete_draft", confirm_args, confirm_token, summarize)
    if not confirmed:
        return message

    try:
        client.delete_draft(draft_id)
        return f"Draft {draft_id} deleted."
    except Exception as e:
        return _friendly(f"failed to delete draft {draft_id}", e)


@mcp.tool(description="Add one or more tags to a post (draft or published).")
def add_tags(post_id: str, tags: List[str]) -> str:
    client = _client_or_raise()
    try:
        client.add_tags_to_post(post_id, tags)
        return f"Tags added to post {post_id}: {', '.join(tags)}"
    except Exception as e:
        return _friendly(f"failed to add tags to post {post_id}", e)


def main() -> None:
    try:
        mcp.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise


if __name__ == "__main__":
    main()
