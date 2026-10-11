"""Offline HTTP qualification of the locked python-substack client.

All payloads below are synthetic fixtures shaped for python-substack 0.10.0's
parsers. They are evidence about the library/client boundary, not Substack.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
import requests
from substack import Api
from substack.exceptions import SubstackAPIException, SubstackRequestException

from substack_publisher_mcp import auth, pending, server


class FakeSubstack:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.cookie_headers = []
        self.request_headers = []
        self.lock = threading.Lock()

    def response(self, method, path, body):
        with self.lock:
            self.calls.append((method, path, body))
            key = (method, path.split("?", 1)[0])
            queued = self.responses.get(key, [])
            if queued:
                status, payload = queued.pop(0)
                return status, payload
        if path.startswith("/api/v1/user/profile/self"):
            return 200, {"id": 44, "publicationUsers": [{"is_primary": True, "publication": {"id": 9, "name": "Synthetic", "subdomain": "synthetic", "custom_domain": "publication.example"}}]}
        if path.startswith("/sign-in"):
            return 200, {}
        if path.startswith("/api/v1/publication/post-tag"):
            return 200, []
        if path.startswith("/api/v1/subscriptions"):
            return 200, {"publications": [{"hostname": "publication.example", "sections": [{"id": 7, "name": "News"}]}]}
        if path.endswith("/prepublish"):
            return 200, {"status": "ok"}
        return 200, {}


@pytest.fixture
def http_boundary(monkeypatch):
    fake = FakeSubstack()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self._respond()
        def do_POST(self): self._respond()
        def do_PUT(self): self._respond()
        def do_DELETE(self): self._respond()

        def _respond(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw and self.headers.get("Content-Type", "").startswith("application/json") else raw.decode()
            except ValueError:
                body = raw.decode(errors="replace")
            path = self.path
            fake.cookie_headers.append(self.headers.get("Cookie", ""))
            fake.request_headers.append({key.lower(): value for key, value in self.headers.items()})
            status, payload = fake.response(self.command, path, body)
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{service.server_port}"
    original_request = requests.sessions.Session.request
    original_send = requests.sessions.Session.send

    def local_only(session, method, url, *args, **kwargs):
        parsed = urlsplit(url)
        # The client has exactly these synthetic origins. All are routed locally;
        # any unexpected URL is rejected before requests can open a socket.
        assert parsed.hostname in {"127.0.0.1", "substack.com", "publication.example"}, url
        rewritten = origin + (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
        return original_request(session, method, rewritten, *args, **kwargs)

    def local_send_only(session, prepared, **kwargs):
        # Also guard redirects, which requests follows below Session.request.
        parsed = urlsplit(prepared.url)
        assert parsed.hostname == "127.0.0.1" and parsed.port == service.server_port, prepared.url
        return original_send(session, prepared, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", local_only)
    monkeypatch.setattr(requests.sessions.Session, "send", local_send_only)
    try:
        yield fake, origin
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


@pytest.fixture
def api(http_boundary):
    _, origin = http_boundary
    return Api(cookies_string="substack.sid=synthetic-secret", publication_url="https://publication.example", base_url=origin + "/api/v1", timeout=2)


def add_response(fake, method, path, payload, status=200):
    fake.responses.setdefault((method, path), []).append((status, payload))


def test_initialization_is_local_and_auth_cookie_is_only_sent_to_fake_service(api, http_boundary):
    fake, _ = http_boundary
    assert api.publication_url == "https://publication.example/api/v1"
    assert [(m, p.split("?", 1)[0]) for m, p, _ in fake.calls[:2]] == [
        ("GET", "/api/v1/user/profile/self"),
        ("GET", "/sign-in"),
    ]
    assert any("substack.sid=synthetic-secret" in value for value in fake.cookie_headers)


def test_unowned_configured_host_is_rejected_before_publication_request(http_boundary):
    fake, origin = http_boundary

    with pytest.raises(SubstackRequestException, match="publication is unavailable"):
        Api(
            cookies_string="substack.sid=synthetic-secret",
            publication_url="https://attacker.example",
            base_url=origin + "/api/v1",
            timeout=2,
        )

    assert [(method, path.split("?", 1)[0]) for method, path, _ in fake.calls] == [
        ("GET", "/api/v1/user/profile/self"),
    ]
    assert all("attacker.example" not in path for _, path, _ in fake.calls)


def test_locked_client_treats_unfollowed_signin_303_as_initialization_error(http_boundary):
    fake, origin = http_boundary
    add_response(fake, "GET", "/sign-in", {}, status=303)

    with pytest.raises(SubstackAPIException) as caught:
        Api(
            cookies_string="substack.sid=synthetic-secret",
            publication_url="https://publication.example",
            base_url=origin + "/api/v1",
            timeout=2,
        )

    assert caught.value.status_code == 303
    assert [(method, path.split("?", 1)[0]) for method, path, _ in fake.calls] == [
        ("GET", "/api/v1/user/profile/self"),
        ("GET", "/sign-in"),
    ]


def test_password_login_serializes_credentials_only_to_local_boundary(http_boundary):
    fake, origin = http_boundary
    client = Api(email="synthetic@example.test", password="synthetic-password", publication_url="https://publication.example", base_url=origin + "/api/v1", timeout=2)
    assert client.publication_url == "https://publication.example/api/v1"
    method, path, body = next(call for call in fake.calls if call[1] == "/api/v1/login")
    assert method == "POST"
    assert body == {"captcha_response": None, "email": "synthetic@example.test", "for_pub": "", "password": "synthetic-password", "redirect": "/"}
    assert any(headers.get("content-type") == "application/json" for headers in fake.request_headers)


def test_draft_listing_retrieval_and_markdown_export_parse_client_fixtures(api, http_boundary):
    fake, _ = http_boundary
    draft = {"id": 31, "title": "Synthetic title", "subtitle": "Sub", "audience": "everyone", "draft_updated_at": "2030-01-01T00:00:00Z", "draft_body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello **world**"}]}]}}
    add_response(fake, "GET", "/api/v1/drafts", {"posts": [draft]})
    add_response(fake, "GET", "/api/v1/drafts/31", draft)
    assert api.get_drafts(filter="draft", offset=2, limit=5) == [draft]
    exported = api.export_draft_to_markdown(31)
    assert "Hello" in exported["markdown"] and "python-substack-revision:v1" in exported["markdown"]
    assert [(m, p) for m, p, _ in fake.calls[-2:]] == [
        ("GET", "/api/v1/drafts?filter=draft&offset=2&limit=5"),
        ("GET", "/api/v1/drafts/31"),
    ]


def test_published_list_and_section_lookup_use_client_contracts(api, http_boundary):
    fake, _ = http_boundary
    add_response(fake, "GET", "/api/v1/post_management/published", [{"id": 31, "title": "Public"}])
    assert api.get_published_posts(limit=3) == [{"id": 31, "title": "Public"}]
    assert api.get_sections() == [{"id": 7, "name": "News"}]
    assert fake.calls[-2][1].startswith("/api/v1/post_management/published?offset=0&limit=3")
    assert fake.calls[-1][:2] == ("GET", "/api/v1/subscriptions")


def test_markdown_update_checks_revision_then_puts_rendered_document(api, http_boundary):
    fake, _ = http_boundary
    draft = {"id": 31, "title": "Title", "subtitle": "", "audience": "everyone", "draft_body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "before"}]}]}}
    add_response(fake, "GET", "/api/v1/drafts/31", draft)
    add_response(fake, "GET", "/api/v1/drafts/31", draft)
    add_response(fake, "GET", "/api/v1/user/profile/self", {"id": 44})
    add_response(fake, "PUT", "/api/v1/drafts/31", {"id": 31, "updated": True})
    marker = api.export_draft_to_markdown(31)["markdown"]
    result = api.update_draft_from_markdown(31, marker + "\n\nafter")
    assert result["draft"] == {"id": 31, "updated": True}
    put = [c for c in fake.calls if c[0] == "PUT"][-1]
    body = put[2]
    rendered = json.loads(body["draft_body"])
    assert rendered["content"]
    assert "after" in json.dumps(rendered)
    assert [c[0] for c in fake.calls[-4:]] == ["GET", "GET", "GET", "PUT"]


def test_stale_revision_blocks_put_before_any_update(api, http_boundary):
    fake, _ = http_boundary
    draft = {"id": 31, "title": "Title", "draft_body": {"type": "doc", "content": []}}
    add_response(fake, "GET", "/api/v1/drafts/31", draft)
    old_marker = "<!-- python-substack-revision:v1 eyJyZXZpc2lvbiI6Im9sZCJ9 -->\n\ntext"
    with pytest.raises(Exception, match="changed since export"):
        api.update_draft_from_markdown(31, old_marker)
    assert not [c for c in fake.calls if c[0] == "PUT"]


def test_creation_publish_schedule_unschedule_delete_and_tag_request_shapes(api, http_boundary):
    fake, _ = http_boundary
    add_response(fake, "GET", "/api/v1/user/profile/self", {"id": 44})
    add_response(fake, "POST", "/api/v1/drafts", {"id": 31})
    add_response(fake, "POST", "/api/v1/drafts/31/publish", {"published": True})
    add_response(fake, "POST", "/api/v1/drafts/31/scheduled_release", {"scheduled": True})
    add_response(fake, "DELETE", "/api/v1/drafts/31/scheduled_release", {"unscheduled": True})
    add_response(fake, "DELETE", "/api/v1/drafts/31", {"deleted": True})
    add_response(fake, "POST", "/api/v1/publication/post-tag", {"id": 8})
    add_response(fake, "POST", "/api/v1/post/31/tag/8", {"applied": True})
    assert api.create_draft_from_markdown("New", "Body")["draft"]["id"] == 31
    api.publish_draft(31, send=False, share_automatically=True)
    from datetime import datetime, timezone
    api.schedule_draft(31, datetime(2030, 1, 2, tzinfo=timezone.utc))
    api.unschedule_draft(31)
    api.delete_draft(31)
    assert api.add_tags_to_post(31, ["synthetic"])["tags_added"] == [{"applied": True}]
    by_method_path = [(m, p.split("?", 1)[0]) for m, p, _ in fake.calls]
    assert ("POST", "/api/v1/drafts") in by_method_path
    assert ("POST", "/api/v1/drafts/31/publish") in by_method_path
    assert ("POST", "/api/v1/drafts/31/scheduled_release") in by_method_path
    assert by_method_path.count(("DELETE", "/api/v1/drafts/31/scheduled_release")) == 1
    assert by_method_path.count(("DELETE", "/api/v1/drafts/31")) == 1
    assert ("POST", "/api/v1/publication/post-tag") in by_method_path
    assert ("POST", "/api/v1/post/31/tag/8") in by_method_path
    publish_body = next(b for m, p, b in fake.calls if p.endswith("/publish"))
    assert publish_body == {"send": False, "share_automatically": True}
    schedule_body = next(b for m, p, b in fake.calls if p.endswith("/scheduled_release"))
    assert schedule_body == {"trigger_at": "2030-01-02T00:00:00+00:00"}


def test_mcp_confirmation_prevents_first_call_and_partial_tag_failure_is_uncertain(api, http_boundary, monkeypatch, caplog):
    fake, _ = http_boundary
    monkeypatch.setattr(server, "_client", api)
    monkeypatch.setattr(pending, "store", pending.PendingActionStore())
    first = server.add_tags("31", ["synthetic"])
    assert "confirm_token" in first
    assert not [c for c in fake.calls if c[0] == "POST"]
    token = first.split('confirm_token="', 1)[1].split('"', 1)[0]
    add_response(fake, "GET", "/api/v1/publication/post-tag", [])
    add_response(fake, "POST", "/api/v1/publication/post-tag", {"id": 8})
    add_response(fake, "POST", "/api/v1/post/31/tag/8", {"error": "private synthetic response"}, status=503)
    result = server.add_tags("31", ["synthetic"], confirm_token=token)
    assert "outcome is unknown" in result and "Do not retry" in result
    assert "private synthetic response" not in result
    assert "synthetic-secret" not in result + caplog.text
    assert len([c for c in fake.calls if c[0] == "POST"]) == 2


def test_post_failure_is_not_retried_and_cookie_not_in_response_or_errors(api, http_boundary):
    fake, _ = http_boundary
    add_response(fake, "POST", "/api/v1/drafts/31/publish", {"error": "synthetic rejection"}, status=503)
    with pytest.raises(Exception):
        api.publish_draft(31)
    assert len([c for c in fake.calls if c[0] == "POST"]) == 1
    assert all("synthetic-secret" not in json.dumps(c) for c in fake.calls)


def test_configured_client_does_not_retry_delete_after_rate_limit(http_boundary):
    fake, origin = http_boundary
    client = auth._configure_client(Api(
        cookies_string="substack.sid=synthetic-secret",
        publication_url="https://publication.example",
        base_url=origin + "/api/v1",
        timeout=2,
    ))
    add_response(fake, "DELETE", "/api/v1/drafts/31", {"error": "rate limited"}, status=429)
    with pytest.raises(Exception):
        client.delete_draft(31)
    assert len([c for c in fake.calls if c[:2] == ("DELETE", "/api/v1/drafts/31")]) == 1

