from __future__ import annotations

import json
from urllib.parse import urlsplit

import requests
import pytest
from io import BytesIO

from substack_publisher_mcp import live_verify


def test_no_opt_in_makes_no_credential_lookup_or_network_request(monkeypatch):
    monkeypatch.setattr(live_verify.auth, "get_stored_cookie_string", lambda _: pytest.fail("keyring accessed"))
    monkeypatch.setattr(requests, "Session", lambda: pytest.fail("session constructed"))

    report = live_verify.qualify("https://publication.example", opt_in=False)

    assert report["classification"] == "BLOCKED"
    assert report["operations"] == []
    assert any("No request was made" in item for item in report["limitations"])


def test_cli_without_live_flag_makes_no_credential_lookup_or_session(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        live_verify.sys,
        "argv",
        ["substack-publisher-mcp-qualify", "--publication-url", "https://publication.example"],
    )
    monkeypatch.setattr(
        live_verify.auth,
        "get_stored_cookie_string",
        lambda _: pytest.fail("keyring accessed"),
    )
    monkeypatch.setattr(requests, "Session", lambda: pytest.fail("session constructed"))

    live_verify.main()

    output = capsys.readouterr().out
    assert '"classification": "BLOCKED"' in output
    assert "No request was made" in output


def test_restricted_session_allows_only_approved_get_hosts_and_paths():
    session = live_verify.RestrictedSession("publication.example")
    assert session.trust_env is False
    assert session.get_adapter("https://publication.example").max_retries.total == 0
    assert session._approved("GET", "https://substack.com/api/v1/user/profile/self")
    assert session._approved("GET", "https://substack.com/sign-in?redirect=%2F&for_pub=publication")
    assert session._approved("GET", "https://publication.example/api/v1/subscriptions")
    assert session._approved("GET", "https://publication.example/api/v1/drafts/123")
    for method, url in [
        ("POST", "https://publication.example/api/v1/drafts"),
        ("PUT", "https://publication.example/api/v1/drafts/123"),
        ("PATCH", "https://publication.example/api/v1/drafts/123"),
        ("DELETE", "https://publication.example/api/v1/drafts/123"),
        ("GET", "https://evil.example/api/v1/drafts"),
        ("GET", "http://publication.example/api/v1/drafts"),
        ("GET", "https://publication.example/api/v1/drafts/123/../123"),
        ("GET", "https://publication.example.evil.example/api/v1/drafts"),
    ]:
        assert not session._approved(method, url)
    session.close()


def test_entered_session_cookie_is_scoped_to_substack_domain():
    session = live_verify.RestrictedSession("brettousignant.substack.com")
    session.cookies.update({"substack.sid": "synthetic-secret"})

    substack_request = session.prepare_request(
        requests.Request("GET", "https://substack.com/api/v1/user/profile/self")
    )
    publication_request = session.prepare_request(
        requests.Request("GET", "https://brettousignant.substack.com/api/v1/subscriptions")
    )
    custom_domain_request = session.prepare_request(
        requests.Request("GET", "https://publication.example/api/v1/subscriptions")
    )

    assert substack_request.headers.get("Cookie") == "substack.sid=synthetic-secret"
    assert publication_request.headers.get("Cookie") == "substack.sid=synthetic-secret"
    assert "Cookie" not in custom_domain_request.headers
    assert "Cookie" not in session.prepare_request(
        requests.Request("GET", "https://brettousignant.substack.com.evil.test/api/v1/subscriptions")
    ).headers
    # CookieJar domain-matches other Substack subdomains, but the request
    # allowlist still rejects them before the transport can run.
    assert session.prepare_request(requests.Request(
        "GET", "https://other.substack.com/api/v1/user/profile/self"
    )).headers.get("Cookie") == "substack.sid=synthetic-secret"
    with pytest.raises(live_verify.BoundaryError):
        session.get("https://other.substack.com/api/v1/user/profile/self")
    session.close()


@pytest.mark.parametrize(
    "publication_url,publication_host",
    [
        ("https://brettousignant.substack.com", "brettousignant.substack.com"),
        ("https://publication.example", "publication.example"),
    ],
)
def test_locked_client_scopes_cookie_and_custom_domain_transport_isolation(
    monkeypatch, publication_url, publication_host
):
    original_factory = requests.Session
    sent = []
    profile = {
        "id": 44,
        "publicationUsers": [{
            "is_primary": True,
            "publication": {
                "id": 9,
                "name": "Synthetic",
                "subdomain": "brettousignant",
                "custom_domain": (
                    "publication.example"
                    if publication_host == "publication.example"
                    else None
                ),
            },
        }],
    }

    def fake_send(_adapter, prepared, **_kwargs):
        parsed = urlsplit(prepared.url)
        sent.append((parsed.hostname, parsed.path, prepared.headers.get("Cookie")))
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        response.headers["Content-Type"] = "application/json"
        if parsed.path == "/api/v1/user/profile/self":
            payload = profile
        elif parsed.path == "/api/v1/subscriptions":
            payload = {"publications": [{"hostname": publication_host, "sections": []}]}
        else:
            payload = {}
        response._content = json.dumps(payload).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)

    with live_verify.restricted_client(
        publication_url, "substack.sid=synthetic-secret"
    ) as client:
        jar = client._session.cookies
        sid = next(cookie for cookie in jar if cookie.name == "substack.sid")
        assert sid.domain == ".substack.com"
        assert sid.domain_specified is True
        if publication_host == "publication.example":
            # Simulate an SDK or response reintroducing a hostless SID. The
            # transport guard must still keep it off the custom domain.
            jar.set_cookie(requests.cookies.create_cookie(
                "substack.sid", "reintroduced", path="/"
            ))
        client.get_sections()

    assert requests.Session is original_factory
    substack_requests = [row for row in sent if row[0] == "substack.com"]
    assert substack_requests
    assert all(row[2] == "substack.sid=synthetic-secret" for row in substack_requests)
    publication_requests = [row for row in sent if row[0] == publication_host]
    if publication_host == "publication.example":
        assert len(publication_requests) == 1
        assert publication_requests[0][2] is None
    else:
        assert len(publication_requests) == 1
        assert publication_requests[0][2] == "substack.sid=synthetic-secret"


def test_listing_queries_are_bounded():
    session = live_verify.RestrictedSession("publication.example")
    assert session._approved("GET", "https://publication.example/api/v1/drafts?filter=draft&offset=0&limit=1")
    assert session._approved("GET", "https://publication.example/api/v1/post_management/published?offset=0&limit=1&order_by=post_date&order_direction=desc")
    assert not session._approved("GET", "https://publication.example/api/v1/drafts?filter=draft&offset=0&limit=25")
    session.close()


def test_unapproved_method_fails_before_transport(monkeypatch):
    session = live_verify.RestrictedSession("publication.example")
    monkeypatch.setattr(session, "send", lambda *_a, **_k: pytest.fail("transport called"))
    with pytest.raises(live_verify.BoundaryError):
        session.request("POST", "https://publication.example/api/v1/drafts")
    session.close()


def test_transport_enforces_six_request_ceiling(monkeypatch):
    session = live_verify.RestrictedSession("publication.example")
    sent = []

    def fake_adapter_send(_adapter, prepared, **_kwargs):
        sent.append(prepared.url)
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    prepared = requests.Request(
        "GET", "https://publication.example/api/v1/subscriptions"
    ).prepare()

    for _ in range(6):
        session.send(prepared)
    with pytest.raises(live_verify.BoundaryError, match="operation limit"):
        session.send(prepared)

    assert len(sent) == 6
    assert session.operations == 6
    session.close()


def test_redirect_is_rejected_without_following_destination(monkeypatch):
    session = live_verify.RestrictedSession("publication.example")
    sent = []

    def response(_session, prepared, **kwargs):
        sent.append((prepared.url, kwargs.get("allow_redirects")))
        result = requests.Response()
        result.status_code = 302
        result.headers["Location"] = "https://evil.example/collect"
        result.url = prepared.url
        result.request = prepared
        result.raw = BytesIO()
        return result

    monkeypatch.setattr(requests.Session, "send", response)
    with pytest.raises(live_verify.BoundaryError, match="redirect rejected") as caught:
        session.request("GET", "https://publication.example/api/v1/subscriptions")
    assert len(sent) == 1
    assert sent[0][1] is False
    assert caught.value.http_status == 302
    assert caught.value.host == "publication.example"
    assert caught.value.path == "/api/v1/subscriptions"
    session.close()


@pytest.mark.parametrize("location", [
    "https://evil.example/",
    "http://brettousignant.substack.com/",
    "https://brettousignant.substack.com/?token=x",
    "https://brettousignant.substack.com/#fragment",
    "https://brettousignant.substack.com:444/",
    "https://user@brettousignant.substack.com/",
    "https://brettousignant.substack.com.evil.test/",
    "https://brettousignant.substack.com/%zz",
])
def test_observed_redirect_rejects_unverified_or_malformed_destinations(location):
    assert live_verify.RestrictedSession._observed_redirect_target(location) is None


def test_observed_redirect_requires_exact_source_and_destination():
    session = live_verify.RestrictedSession("brettousignant.substack.com")
    assert session._is_observed_signin(
        "https://substack.com/sign-in?redirect=%2F&for_pub=brettousignant"
    )
    assert not session._is_observed_signin(
        "https://substack.com/sign-in?redirect=%2F&for_pub=another-publication"
    )
    assert not session._observed_redirect_target("/" )
    assert session._observed_redirect_target(
        "https://brettousignant.substack.com/"
    ) == "https://brettousignant.substack.com/"
    session.close()


@pytest.mark.parametrize(
    "status,expected",
    [(401, "AUTH_FAILURE"), (403, "PERMISSION_DENIED"), (404, "ENDPOINT_DRIFT"),
     (429, "RATE_LIMITED"), (503, "TRANSIENT_ERROR")],
)
def test_status_errors_are_classified_without_response_content(status, expected):
    assert live_verify._classify(RuntimeError("secret response content"), status) == expected


def test_timeout_is_transient_not_unsupported():
    assert live_verify._classify(requests.Timeout("private details"), None) == "TRANSIENT_ERROR"


def test_optional_draft_requires_explicit_numeric_identifier(monkeypatch):
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=never-reported")
    monkeypatch.setattr(live_verify, "Api", lambda **_: pytest.fail("client constructed"))
    report = live_verify.qualify("https://publication.example", opt_in=True, draft_id="../123")
    assert report["classification"] == "RESPONSE_CONTRACT_MISMATCH"
    assert "never-reported" not in str(report)


def test_oversized_numeric_draft_id_fails_before_client_or_network(monkeypatch):
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=never-reported")
    monkeypatch.setattr(live_verify, "Api", lambda **_: pytest.fail("client constructed"))

    report = live_verify.qualify(
        "https://publication.example", opt_in=True, draft_id="9" * 5000
    )

    assert report["classification"] == "RESPONSE_CONTRACT_MISMATCH"
    assert report["operations"] == []
    assert any("no request was made" in item for item in report["limitations"])
    assert "never-reported" not in str(report)


def test_no_cookie_authentication_post_path_is_used(monkeypatch):
    monkeypatch.delenv("SUBSTACK_COOKIES_STRING", raising=False)
    monkeypatch.setattr(live_verify.auth, "get_stored_cookie_string", lambda _: None)
    monkeypatch.setattr(live_verify, "Api", lambda **_: pytest.fail("email/password login must not run"))
    report = live_verify.qualify("https://publication.example", opt_in=True)
    assert report["classification"] == "AUTH_FAILURE"


def test_report_never_contains_exception_credentials_or_content(monkeypatch):
    secret = "cookie=secret draft body=private"
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=" + secret)

    def fail_client(**_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(live_verify, "Api", fail_client)
    report = live_verify.qualify("https://publication.example", opt_in=True)
    assert report["classification"] == "TRANSIENT_ERROR"
    assert secret not in str(report)
    assert report["operations"][-1]["error_type"] == "RuntimeError"


def test_session_factory_is_restored_and_restricted_session_closed_on_init_error(
    monkeypatch,
):
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=synthetic")
    factory_before = requests.Session
    closed_sessions = []
    original_close = live_verify.RestrictedSession.close

    def track_close(session):
        closed_sessions.append(session)
        original_close(session)

    monkeypatch.setattr(live_verify.RestrictedSession, "close", track_close)
    monkeypatch.setattr(
        live_verify,
        "Api",
        lambda **_: (_ for _ in ()).throw(RuntimeError("private details")),
    )

    live_verify.qualify("https://publication.example", opt_in=True)

    assert requests.Session is factory_before
    assert len(closed_sessions) == 1


def test_mutation_api_methods_are_not_part_of_verifier_calls(monkeypatch):
    calls = []

    class ReadOnlyApi:
        def __init__(self, **_kwargs):
            calls.append("init")
            self._session = requests.Session()

        def get_sections(self):
            calls.append("get_sections")
            return []

        def get_drafts(self, **kwargs):
            calls.append(("get_drafts", kwargs))
            return []

        def get_published_posts(self, **kwargs):
            calls.append(("get_published_posts", kwargs))
            return []

        def __getattr__(self, name):
            if name in {"create_draft_from_markdown", "update_draft_from_markdown", "publish_draft", "schedule_draft", "unschedule_draft", "delete_draft", "add_tags_to_post"}:
                pytest.fail(f"mutation API {name} reached")
            raise AttributeError(name)

    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=synthetic")
    monkeypatch.setattr(live_verify, "Api", ReadOnlyApi)
    report = live_verify.qualify("https://publication.example", opt_in=True)
    assert report["classification"] == "SUPPORTED"
    assert ("get_drafts", {"filter": "draft", "offset": 0, "limit": 1}) in calls
    assert ("get_published_posts", {"offset": 0, "limit": 1}) in calls


def test_qualification_runs_only_the_five_bounded_gets_and_reports_shapes(monkeypatch):
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=synthetic")
    monkeypatch.setattr(live_verify.auth, "get_stored_cookie_string", lambda _: pytest.fail("keyring fallback"))
    sent = []
    profile = {
        "id": 1,
        "publicationUsers": [{
            "is_primary": True,
            "publication": {"id": 9, "name": "Synthetic", "subdomain": "synthetic", "custom_domain": "publication.example"},
        }],
    }

    def fake_adapter_send(_adapter, prepared, **_kwargs):
        parsed = urlsplit(prepared.url)
        sent.append((prepared.method, parsed.hostname, parsed.path, parsed.query))
        if parsed.path == "/api/v1/user/profile/self":
            payload = profile
        elif parsed.path == "/api/v1/subscriptions":
            payload = {"publications": [{"hostname": "publication.example", "sections": [{"id": 1, "name": "News"}]}]}
        elif parsed.path == "/api/v1/drafts":
            payload = {"posts": [{"id": 123}]}
        elif parsed.path == "/api/v1/post_management/published":
            payload = [{"id": 456}]
        else:
            payload = {}
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_adapter_send)
    report = live_verify.qualify("https://publication.example", opt_in=True)

    assert report["classification"] == "SUPPORTED"
    assert len(sent) == 5
    assert all(method == "GET" for method, *_ in sent)
    assert {host for _, host, *_ in sent} == {"substack.com", "publication.example"}
    assert [item["http_statuses"] for item in report["operations"]] == [[200, 200], [200], [200], [200]]
    assert "Synthetic" not in str(report)
    assert "substack.sid" not in str(report)
