"""Authentication failures must not expose credentials or response details."""

from contextlib import contextmanager
import json
import requests
import pytest
from urllib.parse import urlsplit

from substack_publisher_mcp import auth, live_verify, server, setup_auth


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
    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "private-cookie")
    @contextmanager
    def fail_restricted_client(*_args):
        raise RuntimeError(secret_detail)
        yield

    monkeypatch.setattr(setup_auth, "restricted_client", fail_restricted_client)
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda *_: pytest.fail("failed verification must not store credentials"),
    )

    with pytest.raises(SystemExit):
        setup_auth.main()

    output = capsys.readouterr().out
    assert "Session check failed" in output
    assert "private-cookie" not in output
    assert "private profile" not in output


def test_setup_uses_restricted_client_and_stores_only_after_profile_validation(
    monkeypatch, capsys
):
    publication_url = "https://brettousignant.substack.com"
    cookies = "substack.sid=synthetic-secret"
    events = []

    class Client:
        def get_user_profile(self):
            events.append("validate")
            return {"id": 41, "name": "Synthetic User"}

    @contextmanager
    def restricted_client(url, supplied_cookies):
        events.append(("restricted", url, supplied_cookies))
        yield Client()

    monkeypatch.setattr("builtins.input", lambda _: publication_url)
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: cookies)
    monkeypatch.setattr(setup_auth, "restricted_client", restricted_client)
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda url, value: events.append(("store", url, value)),
    )

    setup_auth.main()

    assert events == [
        ("restricted", publication_url, cookies),
        "validate",
        ("store", publication_url, cookies),
    ]
    output = capsys.readouterr().out
    assert "Synthetic User" not in output
    assert cookies not in output
    assert "Session validated and saved" in output


@pytest.mark.parametrize("profile", [
    "private-cookie", {}, {"name": "Synthetic User"}, {"id": True},
    {"id": 0}, {"id": -1}, {"id": "44"}, {"id": "user-44"},
])
def test_setup_does_not_store_without_positive_profile_identity(monkeypatch, profile):
    @contextmanager
    def restricted_client(*_args):
        class Client:
            def get_user_profile(self):
                return profile

        yield Client()

    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "private-cookie")
    monkeypatch.setattr(setup_auth, "restricted_client", restricted_client)
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda *_: pytest.fail("invalid profile must not persist credentials"),
    )
    with pytest.raises(SystemExit):
        setup_auth.main()


def test_setup_does_not_store_on_inconclusive_redirect_and_sanitizes_location(
    monkeypatch, capsys
):
    from substack_publisher_mcp import live_verify

    secret = "synthetic-cookie"
    location_secret = "https://redirect-user:redirect-pass@evil.example/sign-in?token=private-query"
    sent = []
    sessions = []
    closed = []
    original_factory = requests.Session
    original_close = live_verify.RestrictedSession.close
    original_init = live_verify.RestrictedSession.__init__

    def track_init(session, publication_host):
        original_init(session, publication_host)
        sessions.append(session)

    def track_close(session):
        closed.append(session)
        original_close(session)

    monkeypatch.setattr(live_verify.RestrictedSession, "__init__", track_init)
    monkeypatch.setattr(live_verify.RestrictedSession, "close", track_close)

    def fake_adapter_send(_adapter, prepared, **_kwargs):
        sent.append((prepared.method, prepared.url, prepared.headers.get("Cookie")))
        response = requests.Response()
        response.url = prepared.url
        response.request = prepared
        if prepared.path_url.startswith("/api/v1/user/profile/self"):
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = (
                b'{"id":44,"publicationUsers":[{"is_primary":true,'
                b'"publication":{"id":9,"subdomain":"brettousignant",'
                b'"custom_domain":null}}]}'
            )
            response._content_consumed = True
        else:
            response.status_code = 302
            response.headers["Location"] = location_secret
            response._content = b"private response body must not be exposed"
            response._content_consumed = True
        return response

    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: secret)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda *_: pytest.fail("inconclusive redirect must not persist credentials"),
    )

    with pytest.raises(SystemExit):
        setup_auth.main()

    output = capsys.readouterr().out
    assert "substack.com/sign-in" in output
    assert "HTTP 302" in output
    assert "classification: REDIRECT" in output
    assert "inconclusive" in output
    assert secret not in output
    assert location_secret not in output
    assert "evil.example" not in output
    assert "private-query" not in output
    assert "redirect-user" not in output
    assert "redirect-pass" not in output
    assert "private response body" not in output
    assert len(sent) == 2
    assert all(method == "GET" for method, *_ in sent)
    assert all("evil.example" not in url for _, url, _ in sent)
    assert all(cookie == "substack.sid=synthetic-cookie" for _, _, cookie in sent)
    assert len(sessions) == 1
    assert closed == sessions
    assert requests.Session is original_factory


def test_setup_does_not_store_when_profile_endpoint_rejects_session(monkeypatch, capsys):
    @contextmanager
    def unauthorized_client(*_args):
        exc = RuntimeError("private response body and cookie")
        exc.http_status = 401
        exc.http_host = "substack.com"
        exc.http_path = "/api/v1/user/profile/self"
        raise exc
        yield

    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "synthetic-cookie")
    monkeypatch.setattr(setup_auth, "restricted_client", unauthorized_client)
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda *_: pytest.fail("401 must not persist credentials"),
    )

    with pytest.raises(SystemExit):
        setup_auth.main()

    output = capsys.readouterr().out
    assert "substack.com/api/v1/user/profile/self" in output
    assert "HTTP 401" in output
    assert "classification: AUTH_FAILURE" in output
    assert "private response body" not in output
    assert "synthetic-cookie" not in output


def test_setup_initialization_and_profile_use_restricted_session(monkeypatch):
    from substack_publisher_mcp import live_verify

    sessions = []
    closed = []
    original_factory = requests.Session
    original_close = live_verify.RestrictedSession.close
    sent = []

    class SyntheticApi:
        def __init__(self, **_kwargs):
            self._session = requests.Session()
            sessions.append(self._session)
            self._session.get("https://substack.com/api/v1/user/profile/self")

        def get_user_profile(self):
            self._session.get("https://substack.com/api/v1/user/profile/self")
            return {"id": 1}

    def fake_send(_adapter, prepared, **kwargs):
        sent.append((prepared.method, prepared.url, kwargs.get("timeout"), kwargs.get("verify")))
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        return response

    monkeypatch.setattr(live_verify, "Api", SyntheticApi)
    monkeypatch.setattr(
        live_verify.RestrictedSession,
        "close",
        lambda session: (closed.append(session), original_close(session))[1],
    )
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "synthetic-cookie")
    monkeypatch.setattr(setup_auth, "store_cookie_string", lambda *_: None)

    setup_auth.main()

    assert requests.Session is original_factory
    assert len(sessions) == 1
    assert sessions[0].trust_env is False
    assert sessions[0].get_adapter("https://").max_retries.total == 0
    assert len(sent) == 2
    assert all(method == "GET" for method, *_ in sent)
    assert all(timeout == (5, 10) for _, _, timeout, _ in sent)
    assert sessions[0].operations == 2
    assert closed == sessions


def test_setup_success_uses_locked_client_then_persists_after_profile_id(
    monkeypatch, capsys
):
    from substack_publisher_mcp import live_verify

    events = []
    original_factory = requests.Session
    original_close = live_verify.RestrictedSession.close

    profile = {
        "id": 44,
        "publicationUsers": [{
            "is_primary": True,
            "publication": {
                "id": 9,
                "name": "Synthetic",
                "subdomain": "brettousignant",
                "custom_domain": None,
            },
        }],
    }

    def fake_adapter_send(_adapter, prepared, **_kwargs):
        events.append((prepared.method, prepared.url, prepared.headers.get("Cookie")))
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        response.headers["Content-Type"] = "application/json"
        response._content = (
            json.dumps(profile).encode()
            if prepared.path_url.startswith("/api/v1/user/profile/self")
            else b"{}"
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    monkeypatch.setattr(
        live_verify.RestrictedSession,
        "close",
        lambda session: (events.append("closed"), original_close(session))[1],
    )
    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "synthetic-cookie")
    monkeypatch.setattr(
        setup_auth,
        "store_cookie_string",
        lambda url, cookie: events.append(("stored", url, cookie)),
    )

    setup_auth.main()

    assert requests.Session is original_factory
    assert events == [
        ("GET", "https://substack.com/api/v1/user/profile/self", "substack.sid=synthetic-cookie"),
        ("GET", "https://substack.com/sign-in?redirect=%2F&for_pub=brettousignant", "substack.sid=synthetic-cookie"),
        ("GET", "https://substack.com/api/v1/user/profile/self", "substack.sid=synthetic-cookie"),
        "closed",
        ("stored", "https://brettousignant.substack.com", "substack.sid=synthetic-cookie"),
    ]
    assert "synthetic-cookie" not in capsys.readouterr().out


@pytest.mark.parametrize("entered", [
    "substack.sid=synthetic-value", "synthetic-value",
])
def test_setup_normalizes_cookie_pair_and_only_saves_after_validation(monkeypatch, capsys, entered):
    events = []

    class Client:
        def get_user_profile(self):
            events.append("validate")
            return {"id": 1}

    @contextmanager
    def restricted_client(url, cookie):
        events.append(("request", url, cookie))
        yield Client()

    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com/")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: entered)
    monkeypatch.setattr(setup_auth, "restricted_client", restricted_client)
    monkeypatch.setattr(setup_auth, "store_cookie_string", lambda url, cookie: events.append(("store", url, cookie)))

    setup_auth.main()

    expected = "substack.sid=synthetic-value"
    assert events == [
        ("request", "https://brettousignant.substack.com", expected),
        "validate",
        ("store", "https://brettousignant.substack.com", expected),
    ]
    assert expected not in capsys.readouterr().out


@pytest.mark.parametrize("entered", [
    "", "Cookie: substack.sid=secret", "Authorization: Bearer secret",
    "other=value", "substack.sid=", "substack.sid=a; other=b", "bad value",
])
def test_setup_rejects_bad_cookie_before_network_or_keyring(monkeypatch, capsys, entered):
    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: entered)
    monkeypatch.setattr(setup_auth, "restricted_client", lambda *_: pytest.fail("network must not run"))
    monkeypatch.setattr(setup_auth, "store_cookie_string", lambda *_: pytest.fail("keyring must not run"))
    with pytest.raises(SystemExit):
        setup_auth.main()
    output = capsys.readouterr().out
    assert "secret" not in output
    assert "does not look like a substack.sid value" in output


@pytest.mark.parametrize("url", [
    "http://brettousignant.substack.com", "https://brettousignant.substack.com/login",
    "https://other.substack.com", "https://brettousignant.substack.com?x=1",
])
def test_setup_rejects_wrong_publication_before_cookie_prompt(monkeypatch, capsys, url):
    monkeypatch.setattr("builtins.input", lambda _: url)
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda *_: pytest.fail("cookie must not be requested"))
    monkeypatch.setattr(setup_auth, "restricted_client", lambda *_: pytest.fail("network must not run"))
    with pytest.raises(SystemExit):
        setup_auth.main()
    assert "https://brettousignant.substack.com" in capsys.readouterr().out


def test_setup_reports_keyring_failure_after_validation_without_cookie(monkeypatch, capsys):
    secret = "synthetic-secret"

    class Client:
        def get_user_profile(self):
            return {"id": 1}

    @contextmanager
    def restricted_client(*_args):
        yield Client()

    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: secret)
    monkeypatch.setattr(setup_auth, "restricted_client", restricted_client)
    monkeypatch.setattr(setup_auth, "store_cookie_string", lambda *_: (_ for _ in ()).throw(RuntimeError(secret)))
    with pytest.raises(SystemExit):
        setup_auth.main()
    output = capsys.readouterr().out
    assert "Windows could not save it to the OS keyring" in output
    assert secret not in output


@pytest.mark.parametrize("location,shown", [
    ("https://next.substack.com/continue?token=private#secret", "next.substack.com/continue"),
    ("https://user:password@evil.example/private?token=private", None),
    ("https://bad host/private?token=private", None),
])
def test_redirect_diagnostic_uses_only_two_locked_client_gets_and_sanitizes_location(
    monkeypatch, capsys, location, shown
):
    monkeypatch.setattr(setup_auth.sys, "argv", ["setup-auth", "--diagnose-redirect"])
    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "fresh-synthetic-cookie")
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "must-not-be-used")
    monkeypatch.setattr(setup_auth, "store_cookie_string", lambda *_: pytest.fail("must not persist"))
    monkeypatch.setattr(auth, "get_stored_cookie_string", lambda *_: pytest.fail("must not read keyring"))
    sent = []
    redirect_settings = []
    profile = {"id": 7, "publicationUsers": [{
        "is_primary": True,
        "publication": {"id": 9, "name": "Synthetic", "subdomain": "brettousignant", "custom_domain": None},
    }]}

    def fake_adapter_send(_adapter, prepared, **kwargs):
        sent.append((prepared.method, prepared.url, kwargs.get("timeout"), kwargs.get("allow_redirects")))
        response = requests.Response()
        response.status_code = 200 if len(sent) == 1 else 303
        response.url = prepared.url
        response.request = prepared
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(profile).encode() if len(sent) == 1 else b"PRIVATE BODY SECRET"
        response._content_consumed = True
        if len(sent) == 2:
            response.headers["Location"] = location
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    original_send = requests.Session.send
    def record_redirect_setting(session, prepared, **kwargs):
        redirect_settings.append(kwargs.get("allow_redirects"))
        return original_send(session, prepared, **kwargs)
    monkeypatch.setattr(requests.Session, "send", record_redirect_setting)
    original_factory = requests.Session
    closed = []
    original_close = live_verify.RestrictedSession.close
    monkeypatch.setattr(live_verify.RestrictedSession, "close", lambda session: (closed.append(session), original_close(session))[1])

    setup_auth.main()

    output = capsys.readouterr().out
    assert [urlsplit(url).path for method, url, _, _ in sent] == [
        "/api/v1/user/profile/self", "/sign-in",
    ]
    assert all(method == "GET" and timeout == (5, 10) for method, _, timeout, _ in sent)
    assert redirect_settings == [False, False]
    assert all("fresh-synthetic-cookie" not in url for _, url, _, _ in sent)
    assert requests.Session is original_factory
    assert len(closed) == 1
    assert "HTTP 303" in output
    assert (shown in output) if shown else ("evil.example" not in output and "invalid" not in output)
    assert "token=private" not in output
    assert "#secret" not in output
    assert "PRIVATE BODY SECRET" not in output
    assert "password" not in output


def test_redirect_diagnostic_success_closes_session_and_reports_only_statuses(
    monkeypatch, capsys
):
    monkeypatch.setattr(setup_auth.sys, "argv", ["setup-auth", "--diagnose-redirect"])
    monkeypatch.setattr("builtins.input", lambda _: "https://brettousignant.substack.com")
    monkeypatch.setattr(setup_auth.getpass, "getpass", lambda _: "fresh-cookie")
    sent = []

    def fake_adapter_send(_adapter, prepared, **_kwargs):
        sent.append(prepared)
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({"id": 1, "publicationUsers": [{
            "is_primary": True,
            "publication": {"id": 9, "subdomain": "brettousignant", "custom_domain": None},
        }]}).encode() if len(sent) == 1 else b"{}"
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    original_factory = requests.Session
    closed = []
    original_close = live_verify.RestrictedSession.close
    monkeypatch.setattr(live_verify.RestrictedSession, "close", lambda session: (closed.append(session), original_close(session))[1])
    setup_auth.main()

    output = capsys.readouterr().out
    assert len(sent) == 2
    assert all(request.method == "GET" for request in sent)
    assert "HTTP 200, HTTP 200" in output
    assert "fresh-cookie" not in output
    assert requests.Session is original_factory
    assert len(closed) == 1


@pytest.mark.parametrize("exc,status,expected", [
    (setup_auth.BoundaryError("private"), None, "classification: BLOCKED"),
    (setup_auth.BoundaryError("private", http_status=302, host="substack.com", path="/sign-in"), 302, "authentication result inconclusive"),
    (RuntimeError("private"), 401, "classification: AUTH_FAILURE"),
    (RuntimeError("private"), 403, "classification: PERMISSION_DENIED"),
    (RuntimeError("private"), 404, "classification: ENDPOINT_DRIFT"),
    (requests.Timeout("private"), None, "classification: TRANSIENT_ERROR"),
    (requests.ConnectionError("private"), None, "classification: TRANSIENT_ERROR"),
    (ValueError("private"), None, "classification: RESPONSE_CONTRACT_MISMATCH"),
])
def test_setup_diagnostics_are_actionable_and_sanitized(exc, status, expected):
    diagnostic = setup_auth._diagnostic(exc, status)
    assert expected in diagnostic
    assert "private" not in diagnostic
    if status == 302:
        assert "substack.com/sign-in" in diagnostic
        assert "HTTP 302" in diagnostic
        assert "missing or expired" not in diagnostic
        assert "synthetic-secret" not in diagnostic


def test_setup_diagnostic_omits_unapproved_host_and_path_values():
    diagnostic = setup_auth._diagnostic(
        setup_auth.BoundaryError(
            "private", http_status=302, host="evil.example",
            path="/sign-in?token=private-query",
        ),
        302,
    )
    assert "HTTP 302" in diagnostic
    assert "classification: REDIRECT" in diagnostic
    assert "evil.example" not in diagnostic
    assert "private-query" not in diagnostic
