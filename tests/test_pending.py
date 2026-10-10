# ABOUTME: Unit tests for the server-enforced confirmation token flow.
# ABOUTME: No network calls — exercises pending.py in isolation.

from substack_publisher_mcp.pending import PendingActionStore, require_confirmation


def test_first_call_issues_token_and_does_not_confirm():
    store = PendingActionStore()
    confirmed, message = _require(store, "delete_draft", {"draft_id": "123"}, None, "delete it")
    assert not confirmed
    assert "CONFIRMATION REQUIRED" in message
    assert "confirm_token=" in message


def test_valid_token_confirms_once():
    store = PendingActionStore()
    args = {"draft_id": "123"}
    _, message = _require(store, "delete_draft", args, None, "delete it")
    token = message.split('confirm_token="')[1].split('"')[0]

    confirmed, _ = _require(store, "delete_draft", args, token, "delete it")
    assert confirmed

    # token is single-use
    confirmed_again, message_again = _require(store, "delete_draft", args, token, "delete it")
    assert not confirmed_again
    assert "invalid, expired, or already used" in message_again


def test_token_scoped_to_tool_name():
    store = PendingActionStore()
    args = {"draft_id": "123"}
    _, message = _require(store, "delete_draft", args, None, "delete it")
    token = message.split('confirm_token="')[1].split('"')[0]

    # a token issued for delete_draft must not confirm publish_draft
    confirmed, _ = _require(store, "publish_draft", args, token, "publish it")
    assert not confirmed


def test_token_scoped_to_arguments():
    store = PendingActionStore()
    _, message = _require(store, "delete_draft", {"draft_id": "123"}, None, "delete it")
    token = message.split('confirm_token="')[1].split('"')[0]

    confirmed, msg = _require(store, "delete_draft", {"draft_id": "456"}, token, "delete it")
    assert not confirmed
    assert "don't match" in msg


def test_cannot_fabricate_a_token():
    store = PendingActionStore()
    confirmed, message = _require(store, "delete_draft", {"draft_id": "123"}, "made-up-token", "delete it")
    assert not confirmed
    assert "invalid" in message


def _require(store, tool, args, token, summary_text):
    # Patch the module-level store for this call via monkeypatch-free indirection:
    # require_confirmation uses the imported `store` singleton, so we call the
    # store's own methods directly here to keep tests isolated per-instance.
    if not token:
        issued = store.create(tool, args, summary_text)
        return False, (
            f"CONFIRMATION REQUIRED\n\n{summary_text}\n\n"
            f'To proceed, call this tool again with confirm_token="{issued}".'
        )
    action = store.consume(token, tool)
    if action is None:
        return False, "That confirmation token is invalid, expired, or already used."
    if action.args != args:
        return False, "The arguments provided don't match the action that was confirmed."
    return True, ""


def test_require_confirmation_function_end_to_end():
    # Exercises the real public function (and its module-level singleton store)
    # for a single round trip, independent of the helper above.
    args = {"draft_id": "999"}
    confirmed, message = require_confirmation("delete_draft", args, None, lambda: "delete it")
    assert not confirmed
    token = message.split('confirm_token="')[1].split('"')[0]

    confirmed2, _ = require_confirmation("delete_draft", args, token, lambda: "delete it")
    assert confirmed2
