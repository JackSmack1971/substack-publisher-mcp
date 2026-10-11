"""Opt-in, read-only live compatibility qualification for Substack.

This command is deliberately separate from the MCP server. Its restricted
requests session rejects all methods except GET, redirects, unapproved hosts,
and unapproved paths before a socket can be opened.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter, Retry
from requests.exceptions import RequestException
from substack import Api

from substack_publisher_mcp import auth

_CONSTRUCTION_LOCK = threading.Lock()
_DRAFT_ID = re.compile(r"^[0-9]+$")
_MAX_DRAFT_ID_LENGTH = 20


class BoundaryError(Exception):
    """Request denied by the live qualification boundary."""

    def __init__(self, message: str, *, http_status: int | None = None,
                 host: str | None = None, path: str | None = None,
                 redirect_host: str | None = None, redirect_path: str | None = None,
                 redirect_contract_unverified: bool = False,
                 redirect_query_present: bool = False,
                 redirect_fragment_present: bool = False,
                 redirect_matches_allowlist: bool = False,
                 request_count: int | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.host = host
        self.path = path
        self.redirect_host = redirect_host
        self.redirect_path = redirect_path
        self.redirect_contract_unverified = redirect_contract_unverified
        self.redirect_query_present = redirect_query_present
        self.redirect_fragment_present = redirect_fragment_present
        self.redirect_matches_allowlist = redirect_matches_allowlist
        self.request_count = request_count


class SubstackCookieJar(requests.cookies.RequestsCookieJar):
    """Keep a manually supplied session cookie scoped to Substack hosts."""

    def update(self, other):
        if isinstance(other, dict):
            for name, value in other.items():
                if name == "substack.sid":
                    self.set(name, value, domain=".substack.com", path="/")
                else:
                    self.set(name, value)
            return
        return super().update(other)


class RestrictedSession(requests.Session):
    """Session permitting a small fixed set of GETs and no redirects/retries."""

    def __init__(self, publication_host: str):
        super().__init__()
        self.cookies = SubstackCookieJar()
        self.trust_env = False
        self.publication_host = publication_host
        self.follow_observed_redirect = True
        self.operations = 0
        self.statuses: list[int] = []
        self.current_status: int | None = None
        self.current_host: str | None = None
        self.current_path: str | None = None
        self._observed_redirect_pending: str | None = None
        self.mount("https://", HTTPAdapter(max_retries=0))
        self.mount("http://", HTTPAdapter(max_retries=0))

    def mount(self, prefix, adapter):
        # python-substack mounts its own retrying adapters during Api.__init__.
        adapter.max_retries = Retry(
            total=0, connect=0, read=0, redirect=0, status=0,
        )
        return super().mount(prefix, adapter)

    def _approved(self, method: str, url: str) -> bool:
        if method.upper() != "GET":
            return False
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path
        if host == "substack.com":
            if path == "/api/v1/user/profile/self":
                return not parsed.query
            if path == "/sign-in":
                query = parse_qs(parsed.query)
                return (
                    set(query) == {"redirect", "for_pub"}
                    and query.get("redirect") == ["/"]
                    and len(query.get("for_pub", [])) == 1
                    and re.fullmatch(r"[a-z0-9-]+", query["for_pub"][0]) is not None
                )
            return False
        if host != self.publication_host:
            return False
        if (
            self.publication_host == "brettousignant.substack.com"
            and path == "/"
            and not parsed.query
            and not parsed.fragment
        ):
            return url == self._observed_redirect_pending
        if path == "/api/v1/subscriptions":
            return not parsed.query
        query = parse_qs(parsed.query)
        if path == "/api/v1/drafts":
            return query == {"filter": ["draft"], "offset": ["0"], "limit": ["1"]}
        if path == "/api/v1/post_management/published":
            return query == {
                "offset": ["0"], "limit": ["1"], "order_by": ["post_date"],
                "order_direction": ["desc"],
            }
        return not parsed.query and re.fullmatch(r"/api/v1/drafts/[0-9]+", path) is not None

    def request(self, method, url, *args, **kwargs):
        params = kwargs.get("params")
        if params is not None:
            url = requests.Request(method, url, params=params).prepare().url
            kwargs.pop("params")
        if not self._approved(method, url):
            raise BoundaryError("request denied by read-only qualification boundary")
        self.current_status = None
        self.current_host = urlsplit(url).hostname
        self.current_path = urlsplit(url).path
        kwargs["allow_redirects"] = False
        try:
            response = super().request(method, url, *args, **kwargs)
        except RequestException as exc:
            safe_error = type(exc).__name__
            if isinstance(exc, requests.Timeout):
                raise requests.Timeout(safe_error) from None
            if isinstance(exc, requests.ConnectionError):
                raise requests.ConnectionError(safe_error) from None
            raise RequestException(safe_error) from None
        self.statuses.append(response.status_code)
        if 300 <= response.status_code < 400:
            redirect_contract_unverified = False
            # Live diagnostic evidence establishes this one transition's
            # destination host and root path. Until its query contract is
            # observed, only a query-free absolute Location is admissible.
            if (
                response.status_code == 303
                and self.publication_host == "brettousignant.substack.com"
                and self._is_observed_signin(url)
            ):
                location = response.headers.get("Location")
                target_url = self._observed_redirect_target(location)
                if target_url is not None and self.follow_observed_redirect:
                    response.close()
                    self._observed_redirect_pending = target_url
                    try:
                        return self.request("GET", target_url, *args, **kwargs)
                    finally:
                        self._observed_redirect_pending = None
                redirect_contract_unverified = True
            location = response.headers.get("Location")
            redirect_host, redirect_path, query_present, fragment_present, matches = (
                self._redirect_evidence(location, response.url or url)
            )
            response.close()
            raise BoundaryError(
                "redirect rejected by read-only qualification boundary",
                http_status=response.status_code,
                host=self.current_host,
                path=self.current_path,
                redirect_host=redirect_host,
                redirect_path=redirect_path,
                redirect_contract_unverified=redirect_contract_unverified,
                redirect_query_present=query_present,
                redirect_fragment_present=fragment_present,
                redirect_matches_allowlist=matches,
                request_count=self.operations,
            )
        return response

    @staticmethod
    def _is_observed_signin(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme == "https"
                and parsed.netloc == "substack.com"
                and parsed.path == "/sign-in"
                and parsed.query == "redirect=%2F&for_pub=brettousignant"
                and not parsed.fragment
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _observed_redirect_target(location: str | None) -> str | None:
        """Validate the complete observed destination before dispatch."""
        if not location or any(ord(char) < 0x21 or ord(char) > 0x7E for char in location):
            return None
        try:
            target = urlsplit(location)
            return location if (
                target.scheme == "https"
                and target.netloc == "brettousignant.substack.com"
                and target.hostname == "brettousignant.substack.com"
                and target.path == "/"
                and not target.query
                and not target.fragment
                and "?" not in location
                and "#" not in location
                and not target.username
                and not target.password
                and target.port is None
            ) else None
        except (ValueError, TypeError):
            return None

    @classmethod
    def _redirect_evidence(
        cls, location: str | None, base_url: str,
    ) -> tuple[str | None, str | None, bool, bool, bool]:
        """Return sanitized redirect facts without retaining URL components."""
        if not location:
            return None, None, False, False, False
        try:
            target = urlsplit(urljoin(base_url, location))
            host = target.hostname
            path = target.path or "/"
            valid_host = bool(host) and all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in host.rstrip(".").split(".")
            )
            safe_destination = (
                target.scheme == "https" and valid_host and not target.username
                and not target.password and target.port in (None, 443)
                and all(0x21 <= ord(char) <= 0x7E and char != "\\" for char in path)
            )
            return (
                host.lower().rstrip(".") if safe_destination else None,
                path if safe_destination else None,
                "?" in location.split("#", 1)[0], "#" in location,
                cls._observed_redirect_target(location) is not None,
            )
        except (ValueError, TypeError):
            # If parsing fails, report delimiter presence conservatively and
            # never expose a destination or treat it as allowlisted.
            return None, None, "?" in location, "#" in location, False

    def send(self, request, **kwargs):
        # Guard the actual transport boundary too, including any SDK code that
        # might bypass Session.request in a future library release.
        if not self._approved(request.method, request.url):
            raise BoundaryError("request denied by read-only qualification boundary")
        if self.operations >= 6:
            raise BoundaryError("qualification operation limit reached")
        self.operations += 1
        self.current_status = None
        self.current_host = urlsplit(request.url).hostname
        self.current_path = urlsplit(request.url).path
        request_host = (self.current_host or "").lower().rstrip(".")
        if request_host != "substack.com" and not request_host.endswith(".substack.com"):
            # Defense in depth: the SDK or a response may add cookies after
            # construction. Never forward any cookie to a custom publication
            # host, even if the jar's contents or domain attributes change.
            request.headers.pop("Cookie", None)
        kwargs["timeout"] = (5, 10)
        kwargs["allow_redirects"] = False
        response = super().send(request, **kwargs)
        self.current_status = response.status_code
        return response


def _publication(publication_url: str) -> tuple[str, str]:
    parsed = urlsplit(publication_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("publication must be a plain https://host URL")
    host = parsed.hostname.lower().rstrip(".")
    labels = host.split(".")
    if host == "substack.com" or not labels or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("publication host is not an approved publication identity")
    return host, f"https://{host}"


@contextmanager
def restricted_client(
    publication_url: str, cookies: str, *, follow_observed_redirect: bool = True,
):
    """Construct a Substack client entirely inside the qualification boundary."""
    host, canonical_url = _publication(publication_url)
    session = RestrictedSession(host)
    session.follow_observed_redirect = follow_observed_redirect
    original_factory = requests.Session
    try:
        # Api sends requests during construction. Keep those requests within
        # this same restricted session, then restore the process factory.
        with _CONSTRUCTION_LOCK:
            requests.Session = lambda: session
            try:
                try:
                    client = Api(cookies_string=cookies, publication_url=canonical_url)
                except Exception as exc:
                    # Preserve only the numeric status needed for safe setup
                    # diagnostics; callers must never print provider exception text.
                    try:
                        exc.http_status = session.current_status
                        exc.http_host = session.current_host
                        exc.http_path = session.current_path
                    except Exception:
                        pass
                    raise
            finally:
                requests.Session = original_factory
        yield client
    finally:
        requests.Session = original_factory
        session.close()


def _shape(value, kind: str) -> dict:
    if kind == "sections":
        valid = isinstance(value, list) and all(isinstance(item, dict) for item in value)
        return {"type": "list", "count": len(value) if isinstance(value, list) else None, "valid": valid}
    if kind == "draft":
        valid = isinstance(value, dict) and isinstance(value.get("markdown"), str) and isinstance(value.get("draft"), dict)
        return {"type": "draft", "valid": valid, "markdown_present": valid}
    valid = isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return {"type": type(value).__name__, "count": len(value) if isinstance(value, list) else None, "valid": valid}


def _classify(exc: Exception, status: int | None) -> str:
    if isinstance(exc, BoundaryError):
        return "BLOCKED"
    if status == 401:
        return "AUTH_FAILURE"
    if status == 403:
        return "PERMISSION_DENIED"
    if status == 404:
        return "ENDPOINT_DRIFT"
    if status in {400, 405}:
        return "ENDPOINT_DRIFT"
    if status == 429:
        return "RATE_LIMITED"
    if status is not None and 500 <= status:
        return "TRANSIENT_ERROR"
    if isinstance(exc, (requests.Timeout,)):
        return "TRANSIENT_ERROR"
    if isinstance(exc, ValueError):
        return "RESPONSE_CONTRACT_MISMATCH"
    if isinstance(exc, (KeyError, TypeError)):
        return "RESPONSE_CONTRACT_MISMATCH"
    return "TRANSIENT_ERROR"


def _runtime_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def qualify(publication_url: str, *, opt_in: bool, draft_id: str | None = None) -> dict:
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runtime": {"python": sys.version.split()[0], "python_substack": _runtime_version("python-substack"), "requests": _runtime_version("requests")},
        "publication": None,
        "operations": [],
        "classification": "BLOCKED",
        "limitations": [
            "Read-only checks only; live results apply to this account, publication, and time.",
            "Initialization checks authenticated profile access and publication selection; cookie authentication is required.",
            "No article or draft content is included in this report.",
        ],
    }
    if not opt_in:
        report["limitations"].append("No request was made because explicit --live opt-in was absent.")
        return report
    try:
        host, canonical_url = _publication(publication_url)
    except ValueError as exc:
        report["limitations"].append(str(exc))
        return report
    report["publication"] = host
    if draft_id is not None and (
        len(draft_id) > _MAX_DRAFT_ID_LENGTH or not _DRAFT_ID.fullmatch(draft_id)
    ):
        report["classification"] = "RESPONSE_CONTRACT_MISMATCH"
        report["limitations"].append(
            "The optional draft identifier must contain at most 20 digits; no request was made."
        )
        return report

    # Email/password login in python-substack sends POST; this verifier supports
    # only existing cookie credentials to keep the entire construction GET-only.
    try:
        cookies = os.environ.get("SUBSTACK_COOKIES_STRING")
        if not cookies:
            cookies = auth.get_stored_cookie_string(publication_url)
    except Exception as exc:
        report["classification"] = "BLOCKED"
        report["operations"].append({"operation": "credential_lookup", "status": "failed", "error_type": type(exc).__name__})
        report["limitations"].append("Existing credential storage could not be read; no network request was made.")
        return report
    if not cookies:
        report["classification"] = "AUTH_FAILURE"
        report["limitations"].append("No cookie credential was available from the existing environment/keyring sources.")
        return report

    session = None
    try:
        with restricted_client(canonical_url, cookies) as client:
            session = client._session
            _qualify_client(report, client, session, draft_id)
    except Exception as exc:
        status = session.current_status if session is not None else None
        classification = _classify(exc, status)
        report["operations"].append({"operation": current_operation if "current_operation" in locals() else "authentication_and_client_initialization", "status": "failed", "http_status": status, "error_type": type(exc).__name__})
        report["classification"] = classification
        if classification == "RESPONSE_CONTRACT_MISMATCH":
            report["limitations"].append("A required response did not match the sanitized expected shape.")
        elif classification == "BLOCKED":
            report["limitations"].append("The restricted request boundary denied the operation or destination.")
    return report


def _qualify_client(report: dict, client: Api, session: RestrictedSession, draft_id: str | None) -> None:
        report["operations"].append({
            "operation": "authentication_and_client_initialization",
            "status": "completed",
            "http_statuses": session.statuses[:],
        })
        checks = [
            ("publication_sections", lambda: client.get_sections(), "sections"),
            ("draft_listing_limit_1", lambda: client.get_drafts(filter="draft", offset=0, limit=1), "list"),
            ("published_listing_limit_1", lambda: client.get_published_posts(offset=0, limit=1), "list"),
        ]
        if draft_id is not None:
            checks.append(("draft_markdown_retrieval", lambda: client.export_draft_to_markdown(int(draft_id)), "draft"))
        for operation, call, kind in checks:
            current_operation = operation
            status_start = len(session.statuses)
            value = call()
            shape = _shape(value, kind)
            if not shape["valid"]:
                raise ValueError(f"unexpected {kind} response shape")
            report["operations"].append({
                "operation": operation,
                "status": "completed",
                "http_statuses": session.statuses[status_start:],
                "response_shape": shape,
            })
        report["classification"] = "SUPPORTED"


def main() -> None:
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description="Opt-in, read-only Substack compatibility qualification")
    parser.add_argument("--publication-url", default=None, help="Publication URL; defaults to SUBSTACK_PUBLICATION_URL")
    parser.add_argument("--live", action="store_true", help="Explicitly authorize bounded external GET requests")
    parser.add_argument("--draft-id", help="Optionally retrieve one draft's Markdown; requires an explicit numeric ID")
    args = parser.parse_args()
    publication_url = args.publication_url or os.getenv("SUBSTACK_PUBLICATION_URL")
    if not publication_url:
        parser.error("--publication-url or SUBSTACK_PUBLICATION_URL is required")
    report = qualify(publication_url, opt_in=args.live, draft_id=args.draft_id)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    main()
