# ABOUTME: MCP server for Substack, built directly on python-substack's
# ABOUTME: markdown-native API (CommonMark via markdown-it-py) instead of a
# ABOUTME: hand-rolled converter, with server-enforced confirmation for
# ABOUTME: destructive actions.

from __future__ import annotations

import ipaddress
import logging
import os
import re
import sys
from datetime import datetime, timezone
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
        "Substack, not literal markdown syntax. All remote mutations "
        "(create_draft, update_draft, publish_draft, schedule_draft, "
        "unschedule_draft, delete_draft, add_tags) "
        "require a two-step confirmation: call once without confirm_token to see "
        "a summary and receive a token, show that summary to the user, then call "
        "again with the token only after the user confirms."
    ),
)

_PUBLICATION_URL: Optional[str] = None
_client: Optional[SubstackApi] = None


def _required_text(value: Any, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"{field} must be a non-empty string."
    return None


def _validate_tags(tags: Any, *, required: bool) -> str | None:
    if tags is None and not required:
        return None
    if not isinstance(tags, list) or (required and not tags):
        if required:
            return "tags must be a non-empty list of non-empty strings."
        return "tags must be a list of non-empty strings or omitted."
    if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        return "Each tag must be a non-empty string."
    return None


def _validate_id(value: Any, field: str) -> str | None:
    return _required_text(value, field)


def _validate_limit(limit: Any) -> str | None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return "limit must be a positive integer."
    return None


def _valid_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_host = hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return False
    return bool(
        ascii_host
        and len(ascii_host) <= 253
        and all(
            len(label) <= 63
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in ascii_host.split(".")
        )
    )


def _publication_url() -> str:
    global _PUBLICATION_URL
    if _PUBLICATION_URL is None:
        url = os.getenv("SUBSTACK_PUBLICATION_URL")
        if not url:
            raise ValueError("SUBSTACK_PUBLICATION_URL must be set")
        parsed = urlparse(url)
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (
            parsed.scheme.lower() != "https"
            or not _valid_hostname(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or not valid_port
            or any(character.isspace() for character in url)
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError(
                "SUBSTACK_PUBLICATION_URL must be a valid HTTPS publication URL without credentials, query, or fragment"
            )
        _PUBLICATION_URL = url
    return _PUBLICATION_URL


def _client_or_raise() -> SubstackApi:
    global _client
    if _client is None:
        try:
            _client = build_client(_publication_url())
        except AuthError as e:
            logger.error("Substack authentication failed (%s)", type(e).__name__)
            raise RuntimeError(
                "Substack authentication failed. Check the configured credentials and publication URL."
            ) from None
        except Exception as e:
            logger.error("Substack client initialization failed (%s)", type(e).__name__)
            raise RuntimeError(
                "Substack client initialization failed. Check the configured credentials and publication URL."
            ) from None
    return _client


def _friendly(prefix: str, e: Exception) -> str:
    # Third-party exception text can contain cookies, response bodies, or other
    # account data. Keep diagnostics useful without copying that text to logs or
    # MCP tool results.
    logger.error("%s (%s)", prefix, type(e).__name__)
    if isinstance(e, DraftConflictError):
        return (
            "Conflict: Substack reported that the draft changed since it was last "
            "fetched. Call get_draft for the current content and revision marker, "
            "then review and reapply your edits. To overwrite despite the conflict, "
            "make a new confirmed call with allow_conflict=true."
        )
    if isinstance(e, SubstackAPIException):
        return "Read-only Substack API request failed. Check the publication and authentication, then retry the read."
    if isinstance(e, _SectionNotFoundError):
        return str(e)
    return f"Error: {prefix}. See server logs for the exception type."


def _mutation_failure(prefix: str, e: Exception, recovery: str) -> str:
    """Report uncertain post-dispatch mutation outcomes without exposing details."""
    if isinstance(e, DraftConflictError):
        return _friendly(prefix, e)
    logger.error("%s (%s)", prefix, type(e).__name__)
    return (
        f"{prefix.capitalize()}: the request may have reached Substack, so the "
        f"outcome is unknown. Do not retry this mutation yet. {recovery}"
    )


# ---- read-only tools -------------------------------------------------


@mcp.tool(description="List recent draft posts (title, id, last updated). Max limit 25.")
def list_drafts(limit: int = 10) -> str:
    error = _validate_limit(limit)
    if error:
        return error
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
    error = _validate_limit(limit)
    if error:
        return error
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
    error = _validate_id(draft_id, "draft_id")
    if error:
        return error
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
                "represented by preservation markers in the Markdown above. Keep those "
                "markers unchanged; update_draft refuses missing or altered unsupported "
                "blocks. This tool does not expose allow_unsupported_change.]"
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


class _SectionNotFoundError(ValueError):
    """A locally generated validation error safe to show to the caller."""


def _resolve_section_id(client: SubstackApi, section_name: str) -> int:
    sections = client.get_sections() or []
    for s in sections:
        if s.get("name", "").strip().lower() == section_name.strip().lower():
            return s.get("id")
    raise _SectionNotFoundError(
        f"No section named '{section_name}' found. Call get_sections to see valid names."
    )


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
    for value, field in ((title, "title"), (markdown, "markdown")):
        error = _required_text(value, field)
        if error:
            return error
    if not isinstance(audience, str) or audience not in {
        "everyone", "only_paid", "founding", "only_free"
    }:
        return "audience must be one of: everyone, only_paid, founding, only_free."
    if not isinstance(subtitle, str):
        return "subtitle must be a string."
    if not isinstance(markdown, str):
        return "markdown must be a string."
    error = _validate_tags(tags, required=False)
    if error:
        return error
    if section_name is not None:
        error = _required_text(section_name, "section_name")
        if error:
            return error
    client = _client_or_raise()
    section_id = None
    if section_name:
        try:
            section_id = _resolve_section_id(client, section_name)
        except Exception as e:
            return _friendly("failed to validate section", e)
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
            f"- Tags: {', '.join(tags) if tags else '[none]'}\n"
            f"- Section: {section_name or '[default]'}\n"
            f"- Content preview: {preview}"
        )

    confirmed, message = require_confirmation("create_draft", confirm_args, confirm_token, summarize)
    if not confirmed:
        return message

    try:
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
        return _mutation_failure(
            "could not confirm draft creation", e,
            "Call list_drafts and inspect for an existing draft with the same title/content before deciding what to do.",
        )


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
    error = _validate_id(draft_id, "draft_id")
    if error:
        return error
    if not isinstance(markdown, str):
        return "markdown must be a string."
    if subtitle is not None and not isinstance(subtitle, str):
        return "subtitle must be a string when provided."
    if not isinstance(allow_conflict, bool):
        return "allow_conflict must be a boolean."
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
            base += f' Subtitle will be set to "{subtitle or "[empty]"}".'
        if allow_conflict:
            base += " Conflict checks will be overridden."
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
        return _mutation_failure(
            f"could not confirm update of draft {draft_id}", e,
            f"Call get_draft for {draft_id} and inspect its current content and revision marker before deciding whether another update is needed.",
        )


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
    error = _validate_id(draft_id, "draft_id")
    if error:
        return error
    if not isinstance(send, bool) or not isinstance(share_automatically, bool):
        return "send and share_automatically must be booleans."
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
        share_note = (
            "Automatic sharing is ON."
            if share_automatically
            else "Automatic sharing is OFF."
        )
        return (
            f'Publish draft "{title}" (ID: {draft_id}) now.\n'
            f"{email_note}\n{share_note}\nThis cannot be undone."
        )

    confirmed, message = require_confirmation("publish_draft", confirm_args, confirm_token, summarize)
    if not confirmed:
        return message

    try:
        client.publish_draft(draft_id, send=send, share_automatically=share_automatically)
        return f"Draft {draft_id} published."
    except Exception as e:
        return _mutation_failure(
            f"could not confirm publication of draft {draft_id}", e,
            f"Call list_published and inspect Substack to verify publication status before considering another publish attempt.",
        )


@mcp.tool(
    description=(
        "Schedule a draft to publish automatically at a future time (ISO 8601 "
        "datetime). Requires confirmation (leave confirm_token empty on the "
        "first call)."
    )
)
def schedule_draft(draft_id: str, publish_at_iso: str, confirm_token: Optional[str] = None) -> str:
    error = _validate_id(draft_id, "draft_id")
    if error:
        return error
    try:
        dt = datetime.fromisoformat(publish_at_iso)
    except (TypeError, ValueError):
        return "publish_at_iso must be a valid ISO 8601 datetime with a timezone offset."
    if dt.tzinfo is None or dt.utcoffset() is None:
        return "publish_at_iso must include a timezone offset, such as +00:00."
    if dt <= datetime.now(timezone.utc).astimezone(dt.tzinfo):
        return "publish_at_iso must be in the future."
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
        client.schedule_draft(draft_id, dt)
        return f"Draft {draft_id} scheduled for {publish_at_iso}."
    except Exception as e:
        return _mutation_failure(
            f"could not confirm scheduling of draft {draft_id}", e,
            "Check the draft's current scheduling state in Substack before changing it again; existing MCP read tools do not expose that state.",
        )


@mcp.tool(
    description=(
        "Cancel a pending scheduled publish for a draft. Requires confirmation "
        "(leave confirm_token empty on the first call)."
    )
)
def unschedule_draft(draft_id: str, confirm_token: Optional[str] = None) -> str:
    error = _validate_id(draft_id, "draft_id")
    if error:
        return error
    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {"draft_id": draft_id}
    confirmed, message = require_confirmation(
        "unschedule_draft",
        confirm_args,
        confirm_token,
        lambda: f"Cancel the scheduled publish for draft {draft_id}.",
    )
    if not confirmed:
        return message

    try:
        client.unschedule_draft(draft_id)
        return f"Schedule cancelled for draft {draft_id}."
    except Exception as e:
        return _mutation_failure(
            f"could not confirm unscheduling of draft {draft_id}", e,
            "Check the draft's current scheduling state in Substack before changing it again; existing MCP read tools do not expose that state.",
        )


@mcp.tool(
    description=(
        "Permanently delete a draft. Cannot be undone. Requires confirmation "
        "(leave confirm_token empty on the first call)."
    )
)
def delete_draft(draft_id: str, confirm_token: Optional[str] = None) -> str:
    error = _validate_id(draft_id, "draft_id")
    if error:
        return error
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
        return _mutation_failure(
            f"could not confirm deletion of draft {draft_id}", e,
            f"Call list_drafts to verify whether draft {draft_id} still exists before deciding what to do.",
        )


@mcp.tool(
    description=(
        "Add one or more tags to a post (draft or published). Requires "
        "confirmation (leave confirm_token empty on the first call)."
    )
)
def add_tags(post_id: str, tags: List[str], confirm_token: Optional[str] = None) -> str:
    error = _validate_id(post_id, "post_id") or _validate_tags(tags, required=True)
    if error:
        return error
    client = _client_or_raise()
    confirm_args: Dict[str, Any] = {"post_id": post_id, "tags": tags}
    confirmed, message = require_confirmation(
        "add_tags",
        confirm_args,
        confirm_token,
        lambda: f"Add these tags to post {post_id}: {', '.join(tags)}",
    )
    if not confirmed:
        return message

    try:
        client.add_tags_to_post(post_id, tags)
        return f"Tags added to post {post_id}: {', '.join(tags)}"
    except Exception as e:
        return _mutation_failure(
            f"could not confirm tags for post {post_id}", e,
            "Inspect the post's current tags in Substack before adding them again; existing MCP read tools do not expose post tags.",
        )


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
