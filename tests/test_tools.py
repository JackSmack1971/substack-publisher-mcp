"""Mock only the Substack boundary; use real tools and confirmation logic."""

from datetime import datetime
from unittest.mock import Mock

import pytest
from substack import Api
from substack.exceptions import DraftConflictError

from substack_publisher_mcp import pending, server


@pytest.fixture
def client(monkeypatch):
    client = Mock(spec=Api)
    client.get_draft.return_value = {"id": "123", "draft_title": "Example"}
    client.create_draft_from_markdown.return_value = {"draft": {"id": "123"}}
    client.get_sections.return_value = [{"id": 7, "name": "News"}]
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setattr(pending, "store", pending.PendingActionStore())
    return client


def token_from(message):
    assert "CONFIRMATION REQUIRED" in message
    return message.split('confirm_token="')[1].split('"')[0]


CASES = [
    ("create_draft", dict(title="Title", markdown="**body**", subtitle="Sub",
                          audience="only_paid", tags=["one", "two"], section_name=" news "),
     "create_draft_from_markdown", (), dict(title="Title", markdown="**body**",
         subtitle="Sub", audience="only_paid", tags=["one", "two"], draft_section_id=7)),
    ("update_draft", dict(draft_id="123", markdown="<!-- revision -->\nEdited",
                          subtitle="", allow_conflict=True),
     "update_draft_from_markdown", (), dict(draft_id="123", markdown="<!-- revision -->\nEdited",
                                           subtitle="", allow_conflict=True)),
    ("publish_draft", dict(draft_id="123", send=False, share_automatically=True),
     "publish_draft", ("123",), dict(send=False, share_automatically=True)),
    ("schedule_draft", dict(draft_id="123", publish_at_iso="2030-01-02T12:30:00+00:00"),
     "schedule_draft", ("123", datetime.fromisoformat("2030-01-02T12:30:00+00:00")), {}),
    ("delete_draft", dict(draft_id="123"), "delete_draft", ("123",), {}),
    ("unschedule_draft", dict(draft_id="123"), "unschedule_draft", ("123",), {}),
    ("add_tags", dict(post_id="123", tags=["news", "updates"]),
     "add_tags_to_post", ("123", ["news", "updates"]), {}),
]


@pytest.mark.parametrize("name,kwargs,method,args,expected", CASES)
def test_confirmation_then_exact_forwarding(client, name, kwargs, method, args, expected):
    tool = getattr(server, name)
    token = token_from(tool(**kwargs))
    for _, _, mutation, _, _ in CASES:
        getattr(client, mutation).assert_not_called()
    response = tool(**kwargs, confirm_token=token)
    assert "Error" not in response
    getattr(client, method).assert_called_once_with(*args, **expected)
    assert "invalid" in tool(**kwargs, confirm_token=token)
    getattr(client, method).assert_called_once()


@pytest.mark.parametrize("name,kwargs,method,args,expected", CASES)
def test_changed_arguments_do_not_mutate(client, name, kwargs, method, args, expected):
    token = token_from(getattr(server, name)(**kwargs))
    changed = dict(kwargs)
    key = "title" if name == "create_draft" else "post_id" if name == "add_tags" else "draft_id"
    changed[key] = "different"
    assert "don't match" in getattr(server, name)(**changed, confirm_token=token)
    getattr(client, method).assert_not_called()


@pytest.mark.parametrize("name,kwargs,other_name,other_kwargs,method", [
    ("unschedule_draft", {"draft_id": "123"}, "add_tags",
     {"post_id": "123", "tags": ["news"]}, "unschedule_draft"),
    ("add_tags", {"post_id": "123", "tags": ["news"]}, "unschedule_draft",
     {"draft_id": "123"}, "add_tags_to_post"),
])
def test_other_mutation_token_cannot_confirm_tool(client, name, kwargs, other_name, other_kwargs, method):
    token = token_from(getattr(server, name)(**kwargs))
    assert "invalid" in getattr(server, other_name)(**other_kwargs, confirm_token=token)
    getattr(client, method).assert_not_called()


@pytest.mark.parametrize("name,kwargs,method", [
    ("unschedule_draft", {"draft_id": "123"}, "unschedule_draft"),
    ("add_tags", {"post_id": "123", "tags": ["news"]}, "add_tags_to_post"),
])
def test_mutation_confirmation_expires(client, monkeypatch, name, kwargs, method):
    token = token_from(getattr(server, name)(**kwargs))
    issued_at = pending.store._actions[token].issued_at
    monkeypatch.setattr(pending.time, "time", lambda: issued_at + 301)
    assert "invalid" in getattr(server, name)(**kwargs, confirm_token=token)
    getattr(client, method).assert_not_called()


def test_new_mutation_summaries_name_effect_and_target(client):
    unschedule = server.unschedule_draft("draft-9")
    assert "Cancel the scheduled publish for draft draft-9" in unschedule
    tags = server.add_tags("post-4", ["news", "launch"])
    assert "Add these tags to post post-4: news, launch" in tags


def test_update_conflict_requires_new_confirmation_for_override(client):
    kwargs = dict(draft_id="123", markdown="<!-- revision -->\nEdited")
    token = token_from(server.update_draft(**kwargs))
    client.update_draft_from_markdown.side_effect = DraftConflictError("stale revision")
    response = server.update_draft(**kwargs, confirm_token=token)
    assert "Conflict: stale revision" in response
    assert "get_draft" in response and "allow_conflict=true" in response
    client.update_draft_from_markdown.assert_called_once_with(
        **kwargs, subtitle=None, allow_conflict=False
    )
    assert "invalid" in server.update_draft(**kwargs, allow_conflict=True, confirm_token=token)
    fresh = token_from(server.update_draft(**kwargs))
    assert "don't match" in server.update_draft(**kwargs, allow_conflict=True, confirm_token=fresh)
    client.update_draft_from_markdown.assert_called_once()
    fresh = token_from(server.update_draft(**kwargs, allow_conflict=True))
    client.update_draft_from_markdown.side_effect = None
    assert "updated" in server.update_draft(**kwargs, allow_conflict=True, confirm_token=fresh)
    assert client.update_draft_from_markdown.call_count == 2
    client.update_draft_from_markdown.assert_called_with(**kwargs, subtitle=None, allow_conflict=True)


def test_get_draft_preserves_markdown_and_warns_about_markers(client):
    markdown = "<!-- revision -->\n# Title\n<!-- unsupported marker -->"
    client.export_draft_to_markdown.return_value = {
        "draft": {"id": "123", "draft_title": "Title", "draft_subtitle": "Sub"},
        "markdown": markdown, "unsupported_nodes": [{"type": "poll"}],
    }
    response = server.get_draft("123")
    client.export_draft_to_markdown.assert_called_once_with("123")
    assert markdown in response
    assert "Keep those markers unchanged" in response
    assert "does not expose allow_unsupported_change" in response


@pytest.mark.parametrize("tool,kwargs,method,args,expected", [
    ("list_drafts", {"limit": 50}, "get_drafts", (), {"filter": "draft", "limit": 25}),
    ("list_published", {"limit": 50}, "get_published_posts", (), {"limit": 25}),
])
def test_other_argument_forwarding(client, tool, kwargs, method, args, expected):
    getattr(client, method).return_value = []
    assert "Error" not in getattr(server, tool)(**kwargs)
    getattr(client, method).assert_called_once_with(*args, **expected)
