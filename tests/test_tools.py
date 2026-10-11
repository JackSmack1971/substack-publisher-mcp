"""Mock only the Substack boundary; use real tools and confirmation logic."""

from datetime import datetime
from unittest.mock import Mock

import pytest
from substack import Api
from substack.exceptions import DraftConflictError, SubstackAPIException

from substack_publisher_mcp import pending, server


@pytest.fixture
def client(monkeypatch):
    client = Mock(spec=Api)
    client.get_draft.return_value = {"id": "123", "draft_title": "Example"}
    client.create_draft_from_markdown.return_value = {"draft": {"id": "123"}}
    client.get_sections.return_value = [
        {"id": 7, "name": "News"}, {"id": 8, "name": "Updates"}
    ]
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


def test_summaries_include_material_mutation_options(client):
    create = server.create_draft(
        "Title", "body", tags=["news"], section_name="Updates"
    )
    assert 'Tags: ["news"]' in create
    assert 'Section (untrusted): "Updates"' in create

    update = server.update_draft(
        "123", "replacement", subtitle="New subtitle", allow_conflict=True
    )
    assert 'Subtitle will be set to "New subtitle"' in update
    assert "Conflict checks will be overridden" in update

    publish = server.publish_draft("123", share_automatically=True)
    assert "Automatic sharing is ON" in publish

    client.create_draft_from_markdown.assert_not_called()
    client.update_draft_from_markdown.assert_not_called()
    client.publish_draft.assert_not_called()


def test_publish_defaults_are_bound_and_preserve_email_behavior(client):
    mismatched_token = token_from(server.publish_draft("123"))

    assert "don't match" in server.publish_draft(
        "123", send=False, confirm_token=mismatched_token
    )
    client.publish_draft.assert_not_called()

    token = token_from(server.publish_draft("123"))
    assert "published" in server.publish_draft("123", confirm_token=token)
    client.publish_draft.assert_called_once_with(
        "123", send=True, share_automatically=False
    )


def test_update_conflict_requires_new_confirmation_for_override(client):
    kwargs = dict(draft_id="123", markdown="<!-- revision -->\nEdited")
    token = token_from(server.update_draft(**kwargs))
    client.update_draft_from_markdown.side_effect = DraftConflictError("stale revision")
    response = server.update_draft(**kwargs, confirm_token=token)
    assert "Conflict:" in response
    assert "stale revision" not in response
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


@pytest.mark.parametrize("tool", [server.list_drafts, server.list_published])
@pytest.mark.parametrize("limit", [0, -1, 1.5, "2", True, server.MAX_LIST_LIMIT + 1])
def test_invalid_list_limits_stop_before_api(client, tool, limit):
    assert "positive integer" in tool(limit=limit)
    client.get_drafts.assert_not_called()
    client.get_published_posts.assert_not_called()


@pytest.mark.parametrize("tool,method", [
    (server.list_drafts, "get_drafts"),
    (server.list_published, "get_published_posts"),
])
def test_list_limit_boundaries_and_cap_remain_compatible(client, tool, method):
    client.get_drafts.return_value = []
    client.get_published_posts.return_value = []
    assert "No " in tool(limit=1)
    assert "No " in tool(limit=25)
    assert "No " in tool(limit=26)
    calls = getattr(client, method).call_args_list
    assert [call.kwargs["limit"] for call in calls] == [1, 25, 25]


@pytest.mark.parametrize("kwargs", [
    {"title": " ", "markdown": "body"},
    {"title": "Title", "markdown": "body", "audience": "all"},
    {"title": "Title", "markdown": "body", "audience": []},
    {"title": "Title", "markdown": "body", "tags": [""]},
    {"title": "Title", "markdown": "body", "tags": [None]},
    {"title": "Title", "markdown": "body", "section_name": " "},
])
def test_invalid_create_inputs_never_issue_confirmation_or_mutate(client, kwargs):
    result = server.create_draft(**kwargs)
    assert "CONFIRMATION REQUIRED" not in result
    assert "must" in result or "Each tag" in result
    client.create_draft_from_markdown.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"title": "x" * (server.MAX_TITLE_LENGTH + 1), "markdown": "body"},
    {"title": "Title", "markdown": "x" * (server.MAX_MARKDOWN_LENGTH + 1)},
    {"title": "Title", "markdown": "body", "subtitle": "x" * (server.MAX_SUBTITLE_LENGTH + 1)},
    {"title": "Title", "markdown": "body", "tags": ["ok"] * (server.MAX_TAG_COUNT + 1)},
    {"title": "Title", "markdown": "body", "tags": ["x" * (server.MAX_TAG_LENGTH + 1)]},
    {"title": "Title", "markdown": "body", "section_name": "x" * (server.MAX_SECTION_NAME_LENGTH + 1)},
])
def test_oversized_create_inputs_rejected_before_confirmation_or_api(client, kwargs):
    result = server.create_draft(**kwargs)
    assert "CONFIRMATION REQUIRED" not in result
    assert "must" in result
    client.create_draft_from_markdown.assert_not_called()


def test_oversized_update_body_rejected_before_confirmation_or_api(client):
    result = server.update_draft("123", "x" * (server.MAX_MARKDOWN_LENGTH + 1))
    assert "at most" in result
    assert "CONFIRMATION REQUIRED" not in result
    client.update_draft_from_markdown.assert_not_called()


def test_path_unsafe_and_oversized_ids_are_rejected(client):
    for draft_id in ("../123", "123?x=y", "x" * (server.MAX_ID_LENGTH + 1)):
        assert "draft_id must" in server.get_draft(draft_id)
    client.export_draft_to_markdown.assert_not_called()


def test_oversized_draft_response_is_refused_without_truncation(client):
    client.export_draft_to_markdown.return_value = {
        "draft": {"id": "123"},
        "markdown": "x" * (server.MAX_MARKDOWN_LENGTH + 1),
    }
    result = server.get_draft("123")
    assert "was not returned" in result
    assert len(result) < 200


def test_unknown_section_is_rejected_before_confirmation_or_mutation(client):
    result = server.create_draft("Title", "body", section_name="Missing")
    assert "No section named" in result
    assert "CONFIRMATION REQUIRED" not in result
    client.create_draft_from_markdown.assert_not_called()


@pytest.mark.parametrize("name,kwargs,methods", [
    ("update_draft", {"draft_id": " ", "markdown": "body"}, ["update_draft_from_markdown"]),
    ("update_draft", {"draft_id": "id", "markdown": "body", "allow_conflict": "true"}, ["update_draft_from_markdown"]),
    ("publish_draft", {"draft_id": "", "send": True}, ["get_draft", "publish_draft"]),
    ("publish_draft", {"draft_id": "id", "send": "false"}, ["get_draft", "publish_draft"]),
    ("schedule_draft", {"draft_id": "id", "publish_at_iso": "not-a-date"}, ["schedule_draft"]),
    ("schedule_draft", {"draft_id": "id", "publish_at_iso": "2030-01-01T00:00:00"}, ["schedule_draft"]),
    ("schedule_draft", {"draft_id": "id", "publish_at_iso": "2030-01-01T00:00:00+25:00"}, ["schedule_draft"]),
    ("schedule_draft", {"draft_id": "id", "publish_at_iso": "2000-01-01T00:00:00Z"}, ["schedule_draft"]),
    ("delete_draft", {"draft_id": "\t"}, ["get_draft", "delete_draft"]),
    ("unschedule_draft", {"draft_id": ""}, ["unschedule_draft"]),
    ("add_tags", {"post_id": "id", "tags": []}, ["add_tags_to_post"]),
    ("add_tags", {"post_id": "id", "tags": ["ok", " "]}, ["add_tags_to_post"]),
    ("add_tags", {"post_id": " ", "tags": ["ok"]}, ["add_tags_to_post"]),
])
def test_invalid_mutations_rejected_before_confirmation_and_api(client, name, kwargs, methods):
    result = getattr(server, name)(**kwargs)
    assert "CONFIRMATION REQUIRED" not in result
    for method in methods:
        getattr(client, method).assert_not_called()


def test_empty_read_draft_identifier_is_rejected_before_api(client):
    assert "draft_id must be" in server.get_draft(" ")
    client.export_draft_to_markdown.assert_not_called()


def test_schedule_valid_timezone_boundary_still_requires_confirmation(client):
    result = server.schedule_draft("123", "2030-01-02T12:30:00+00:00")
    assert "CONFIRMATION REQUIRED" in result
    client.schedule_draft.assert_not_called()
    token = token_from(result)
    response = server.schedule_draft(
        "123", "2030-01-02T12:30:00+00:00", confirm_token=token
    )
    assert "scheduled" in response
    client.schedule_draft.assert_called_once_with(
        "123", datetime.fromisoformat("2030-01-02T12:30:00+00:00")
    )


@pytest.mark.parametrize("audience", ["founding", "only_free"])
def test_supported_audiences_remain_forwarded(client, audience):
    kwargs = {"title": "Title", "markdown": "body", "audience": audience}
    token = token_from(server.create_draft(**kwargs))
    assert "created" in server.create_draft(**kwargs, confirm_token=token)
    client.create_draft_from_markdown.assert_called_once_with(
        title="Title", markdown="body", subtitle="", audience=audience,
        tags=None, draft_section_id=None,
    )


def test_add_tags_does_not_impose_an_undocumented_count_limit(client):
    tags = [f"tag-{index}" for index in range(30)]
    token = token_from(server.add_tags("123", tags))
    assert "Tags added" in server.add_tags("123", tags, confirm_token=token)
    client.add_tags_to_post.assert_called_once_with("123", tags)


def test_add_tags_enforces_documented_maximum(client):
    tags = [f"tag-{index}" for index in range(server.MAX_TAG_COUNT + 1)]
    result = server.add_tags("123", tags)
    assert "at most" in result
    assert "CONFIRMATION REQUIRED" not in result
    client.add_tags_to_post.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://publication.example",
    "ftp://publication.example",
    "https://user:pass@publication.example",
    "https://publication.example:bad",
    "https://publication.example/path?query=1",
    "https://publication.example/path",
    "https://.",
    "https:///missing-host",
])
def test_invalid_publication_urls_rejected(monkeypatch, url):
    monkeypatch.setattr(server, "_PUBLICATION_URL", None)
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", url)
    with pytest.raises(ValueError, match="valid HTTPS"):
        server._publication_url()


def test_valid_publication_url_preserves_trailing_slash(monkeypatch):
    monkeypatch.setattr(server, "_PUBLICATION_URL", None)
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "https://publication.example/")
    assert server._publication_url() == "https://publication.example/"


def test_substack_exception_details_are_not_returned_or_logged(client, caplog):
    sensitive_detail = "substack.sid=private-cookie response body: private draft"
    client.get_drafts.side_effect = SubstackAPIException(500, sensitive_detail)

    response = server.list_drafts()

    assert "Substack API request failed" in response
    assert sensitive_detail not in response
    assert sensitive_detail not in caplog.text


def test_conflict_response_does_not_echo_exception_details(client, caplog):
    sensitive_detail = "private response body and credential=secret"
    client.update_draft_from_markdown.side_effect = DraftConflictError(sensitive_detail)
    kwargs = {"draft_id": "123", "markdown": "edited"}
    token = token_from(server.update_draft(**kwargs))

    response = server.update_draft(**kwargs, confirm_token=token)

    assert "Conflict:" in response
    assert "get_draft" in response
    assert sensitive_detail not in response
    assert sensitive_detail not in caplog.text


@pytest.mark.parametrize("name,kwargs,method,recovery", [
    ("create_draft", {"title": "Title", "markdown": "body"}, "create_draft_from_markdown", "list_drafts"),
    ("update_draft", {"draft_id": "123", "markdown": "body"}, "update_draft_from_markdown", "get_draft"),
    ("publish_draft", {"draft_id": "123"}, "publish_draft", "list_published"),
    ("schedule_draft", {"draft_id": "123", "publish_at_iso": "2030-01-02T12:30:00+00:00"}, "schedule_draft", "scheduling state"),
    ("unschedule_draft", {"draft_id": "123"}, "unschedule_draft", "scheduling state"),
    ("delete_draft", {"draft_id": "123"}, "delete_draft", "list_drafts"),
    ("add_tags", {"post_id": "123", "tags": ["news"]}, "add_tags_to_post", "current tags"),
])
def test_ambiguous_mutation_failure_requires_state_inspection_and_fresh_confirmation(
    client, caplog, name, kwargs, method, recovery
):
    secret = "cookie=private response body=do-not-leak"
    getattr(client, method).side_effect = SubstackAPIException(500, secret)
    tool = getattr(server, name)
    token = token_from(tool(**kwargs))

    response = tool(**kwargs, confirm_token=token)

    assert "outcome is unknown" in response
    assert "Do not retry this mutation yet" in response
    assert recovery in response
    assert secret not in response
    assert secret not in caplog.text
    getattr(client, method).assert_called_once()
    assert "invalid" in tool(**kwargs, confirm_token=token)
    getattr(client, method).assert_called_once()
    # Issuing another token confirms intent only; it never repeats the call.
    fresh = token_from(tool(**kwargs))
    assert fresh != token
    getattr(client, method).assert_called_once()


def test_read_only_api_failure_retains_safe_retry_guidance(client, caplog):
    secret = "private cookie and response payload"
    client.get_drafts.side_effect = SubstackAPIException(500, secret)

    response = server.list_drafts()

    assert "Read-only" in response
    assert "retry the read" in response
    assert secret not in response
    assert secret not in caplog.text
