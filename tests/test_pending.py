"""Exercise the production confirmation function with an isolated store."""

import pytest

from substack_publisher_mcp import pending


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(pending, "store", pending.PendingActionStore())


def issue(tool="delete_draft", args=None):
    confirmed, message = pending.require_confirmation(
        tool, args or {"draft_id": "123"}, None, lambda: "delete it"
    )
    assert not confirmed
    assert "CONFIRMATION REQUIRED" in message
    assert "delete it" in message
    return message.split('confirm_token="')[1].split('"')[0]


def test_valid_token_confirms_once_without_rebuilding_summary():
    token = issue()
    def unexpected_summary():
        pytest.fail("confirmation should not rebuild the summary")
    assert pending.require_confirmation(
        "delete_draft", {"draft_id": "123"}, token, unexpected_summary
    ) == (True, "")
    assert "invalid, expired, or already used" in pending.require_confirmation(
        "delete_draft", {"draft_id": "123"}, token, unexpected_summary
    )[1]


@pytest.mark.parametrize("tool,args,expected", [
    ("publish_draft", {"draft_id": "123"}, "invalid"),
    ("delete_draft", {"draft_id": "456"}, "don't match"),
])
def test_mismatch_rejects_and_consumes_token(tool, args, expected):
    token = issue()
    confirmed, message = pending.require_confirmation(tool, args, token, lambda: "unused")
    assert not confirmed
    assert expected in message
    assert not pending.require_confirmation(
        "delete_draft", {"draft_id": "123"}, token, lambda: "unused"
    )[0]


def test_fabricated_token_rejected():
    confirmed, message = pending.require_confirmation(
        "delete_draft", {"draft_id": "123"}, "made-up-token", lambda: "unused"
    )
    assert not confirmed
    assert "invalid" in message


def test_expired_token_rejected(monkeypatch):
    token = issue()
    issued_at = pending.store._actions[token].issued_at
    monkeypatch.setattr(pending.time, "time", lambda: issued_at + 301)
    confirmed, message = pending.require_confirmation(
        "delete_draft", {"draft_id": "123"}, token, lambda: "unused"
    )
    assert not confirmed
    assert "expired" in message
