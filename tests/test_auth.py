"""Authentication failures must not expose credentials or response details."""

import pytest

from substack_publisher_mcp import auth, server, setup_auth


def test_stored_cookie_failure_is_cleared_without_logging_exception_details(
    monkeypatch, caplog
):
    secret_detail = "substack.sid=private-cookie response body: private profile"
    monkeypatch.delenv("SUBSTACK_COOKIES_STRING", raising=False)
    monkeypatch.delenv("SUBSTACK_EMAIL", raising=False)
    monkeypatch.delenv("SUBSTACK_PASSWORD", raising=False)
    monkeypatch.setattr(auth, "get_stored_cookie_string", lambda _: "private-cookie")
    monkeypatch.setattr(
        auth,
        "SubstackApi",
        lambda **_: (_ for _ in ()).throw(RuntimeError(secret_detail)),
    )
    clear_stored = []
    monkeypatch.setattr(auth, "clear_stored_cookie_string", clear_stored.append)

    with pytest.raises(auth.AuthError):
        auth.build_client("https://publication.example")

    assert clear_stored == ["https://publication.example"]
    assert secret_detail not in caplog.text


@pytest.mark.parametrize("failure", [
    auth.AuthError("cookie=private-cookie response body: private profile"),
    RuntimeError("cookie=private-cookie response body: private profile"),
])
def test_client_initialization_failure_is_generic_and_not_logged(
    monkeypatch, caplog, failure
):
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setattr(server, "_publication_url", lambda: "https://publication.example")
    monkeypatch.setattr(server, "build_client", lambda _: (_ for _ in ()).throw(failure))

    with pytest.raises(RuntimeError) as raised:
        server._client_or_raise()

    assert "private-cookie" not in str(raised.value)
    assert "private profile" not in str(raised.value)
    assert "private-cookie" not in caplog.text
    assert "private profile" not in caplog.text


def test_setup_verification_failure_does_not_print_cookie_or_response_details(
    monkeypatch, capsys
):
    secret_detail = "substack.sid=private-cookie response body: private profile"
    monkeypatch.setattr("builtins.input", lambda _: "https://publication.example")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "private-cookie")
    monkeypatch.setattr(
        setup_auth,
        "SubstackApi",
        lambda **_: (_ for _ in ()).throw(RuntimeError(secret_detail)),
    )
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda *_: pytest.fail("failed verification must not store credentials"),
    )

    with pytest.raises(SystemExit):
        setup_auth.main()

    output = capsys.readouterr().out
    assert "Verification failed (RuntimeError)" in output
    assert "private-cookie" not in output
    assert "private profile" not in output
