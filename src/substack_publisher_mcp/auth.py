# ABOUTME: Authentication for the Substack API client.
# ABOUTME: Prefers a pasted cookie string, stored once via the OS keychain (keyring).

from __future__ import annotations

import logging
import os
from types import FunctionType, SimpleNamespace
from urllib.parse import urlsplit

import keyring
import requests
from requests.adapters import Retry
from substack import Api as SubstackApi

logger = logging.getLogger(__name__)

SERVICE_NAME = "substack-publisher-mcp"
COOKIE_KEY = "cookies_string"
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10


class CookieScopedSession(requests.Session):
    """Requests session scoped to authenticated Substack publication hosts."""

    def __init__(self, publication_host: str | None = None):
        super().__init__()
        self._cookie_allowed_hosts = {"substack.com", "www.substack.com"}
        self._publication_host = publication_host

    def send(self, prepared_request, **send_kwargs):
        parsed = urlsplit(prepared_request.url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            raise requests.exceptions.InvalidURL("Blocked non-HTTPS upstream request")
        if parsed.port not in (None, 443):
            raise requests.exceptions.InvalidURL("Blocked upstream port")
        if host not in self._cookie_allowed_hosts:
            raise requests.exceptions.InvalidURL("Blocked unverified redirect host")
        response = super().send(prepared_request, **send_kwargs)
        if (
            host in {"substack.com", "www.substack.com"}
            and parsed.path == "/api/v1/user/profile/self"
            and response.ok
        ):
            try:
                profile = response.json()
                for item in profile.get("publicationUsers", []):
                    publication = item.get("publication") or {}
                    custom = publication.get("custom_domain")
                    subdomain = publication.get("subdomain")
                    candidate = custom or (f"{subdomain}.substack.com" if subdomain else None)
                    if candidate:
                        candidate_host = (urlsplit("//" + candidate).hostname or "").lower().rstrip(".")
                        requested_host = (self._publication_host or "").lower().rstrip(".")
                        if requested_host.startswith("www."):
                            requested_host = requested_host[4:]
                        if candidate_host == requested_host:
                            self._cookie_allowed_hosts.add(candidate_host)
            except (ValueError, AttributeError, TypeError):
                pass
        return response


def _build_cookie_scoped_client(**kwargs) -> SubstackApi:
    """Build python-substack with a session that scopes cookies across redirects.

    python-substack stores pasted cookies without a domain. Requests then sends
    those cookies to every redirect host. Only the authenticated Substack profile
    may add publication hosts to the session's cookie allowlist.
    """
    # Clone the pinned client's constructor with a private requests namespace.
    # This avoids changing python-substack's module globals, even temporarily.
    private_requests = SimpleNamespace(**vars(requests))
    requested_host = urlsplit(str(kwargs.get("publication_url") or "").lower()).hostname
    if not requested_host:
        requested_host = str(kwargs.get("publication_url") or "").lower().strip("/")
    if requested_host.startswith("www."):
        requested_host = requested_host[4:]
    private_requests.Session = lambda: CookieScopedSession(requested_host)
    private_globals = dict(SubstackApi.__init__.__globals__)
    private_globals["requests"] = private_requests
    private_init = FunctionType(
        SubstackApi.__init__.__code__,
        private_globals,
        SubstackApi.__init__.__name__,
        SubstackApi.__init__.__defaults__,
        SubstackApi.__init__.__closure__,
    )
    private_init.__kwdefaults__ = SubstackApi.__init__.__kwdefaults__
    private_init.__annotations__ = SubstackApi.__init__.__annotations__

    class CookieScopedApi(SubstackApi):
        pass

    CookieScopedApi.__init__ = private_init
    return CookieScopedApi(**kwargs)


def _create_client(**kwargs) -> SubstackApi:
    return _configure_client(_build_cookie_scoped_client(**kwargs))


def _bound_retries(client: SubstackApi) -> SubstackApi:
    """Keep only bounded, idempotent GET retries from python-substack."""
    for adapter in client._session.adapters.values():
        retry = adapter.max_retries
        if isinstance(retry, Retry):
            adapter.max_retries = retry.new(
                allowed_methods=frozenset({"GET"}),
                respect_retry_after_header=False,
                backoff_factor=0.25,
            )
    return client


def _configure_client(client: SubstackApi) -> SubstackApi:
    """Apply request timeouts and safe retry policy to the upstream session."""
    client.timeout = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
    original_request = client._session.request

    def request_with_timeout(*args, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = client.timeout
        return original_request(*args, **kwargs)

    client._session.request = request_with_timeout
    return _bound_retries(client)


def store_cookie_string(publication_url: str, cookies_string: str) -> None:
    """Persist a cookie string for a publication in the OS keychain."""
    keyring.set_password(SERVICE_NAME, f"{COOKIE_KEY}:{publication_url}", cookies_string)


def get_stored_cookie_string(publication_url: str) -> str | None:
    return keyring.get_password(SERVICE_NAME, f"{COOKIE_KEY}:{publication_url}")


def clear_stored_cookie_string(publication_url: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, f"{COOKIE_KEY}:{publication_url}")
    except keyring.errors.PasswordDeleteError:
        pass


class AuthError(Exception):
    """Raised when no usable Substack credentials are available."""


def build_client(publication_url: str) -> SubstackApi:
    """Build an authenticated python-substack client.

    Resolution order:
      1. SUBSTACK_COOKIES_STRING env var (semicolon-separated cookie string)
      2. Cookie string stored in the OS keychain by `substack-publisher-mcp-setup`
      3. SUBSTACK_EMAIL / SUBSTACK_PASSWORD env vars (may trigger a CAPTCHA)

    A cookie string is the recommended path: in a logged-in browser, copy the
    `substack.sid` value from DevTools and either set SUBSTACK_COOKIES_STRING
    (as `substack.sid=<value>`) or run the setup command once.
    """
    env_cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    if env_cookies:
        logger.info("Authenticating with SUBSTACK_COOKIES_STRING")
        return _create_client(
            cookies_string=env_cookies,
            publication_url=publication_url,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )

    stored_cookies = get_stored_cookie_string(publication_url)
    if stored_cookies:
        logger.info("Authenticating with stored cookie string")
        try:
            return _create_client(
                cookies_string=stored_cookies,
                publication_url=publication_url,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except Exception as e:
            logger.warning(
                "Stored cookie authentication failed (%s); clearing it",
                type(e).__name__,
            )
            clear_stored_cookie_string(publication_url)

    email = os.getenv("SUBSTACK_EMAIL")
    password = os.getenv("SUBSTACK_PASSWORD")
    if email and password:
        logger.info("Authenticating with email/password")
        try:
            return _create_client(
                email=email,
                password=password,
                publication_url=publication_url,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except Exception as e:
            if "captcha" in str(e).lower():
                raise AuthError(
                    "Substack returned a CAPTCHA challenge. Email/password auth can't "
                    "solve this automatically — run 'substack-publisher-mcp-setup' and "
                    "paste a cookie string from a logged-in browser session instead."
                ) from e
            raise

    raise AuthError(
        "No Substack credentials found. Run 'substack-publisher-mcp-setup' to store a "
        "cookie string, or set SUBSTACK_COOKIES_STRING / SUBSTACK_EMAIL+SUBSTACK_PASSWORD."
    )
