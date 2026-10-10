# ABOUTME: Authentication for the Substack API client.
# ABOUTME: Prefers a pasted cookie string, stored once via the OS keychain (keyring).

from __future__ import annotations

import logging
import os

import keyring
from substack import Api as SubstackApi

logger = logging.getLogger(__name__)

SERVICE_NAME = "substack-publisher-mcp"
COOKIE_KEY = "cookies_string"


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

    A cookie string is the recommended path: open the publication in a logged-in
    browser, copy the Cookie header (or the `substack.sid` cookie value) via dev
    tools, and either set SUBSTACK_COOKIES_STRING or run the setup command once.
    """
    env_cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    if env_cookies:
        logger.info("Authenticating with SUBSTACK_COOKIES_STRING")
        return SubstackApi(cookies_string=env_cookies, publication_url=publication_url)

    stored_cookies = get_stored_cookie_string(publication_url)
    if stored_cookies:
        logger.info("Authenticating with stored cookie string")
        try:
            return SubstackApi(cookies_string=stored_cookies, publication_url=publication_url)
        except Exception as e:
            logger.warning(f"Stored cookie string failed ({e}); clearing it")
            clear_stored_cookie_string(publication_url)

    email = os.getenv("SUBSTACK_EMAIL")
    password = os.getenv("SUBSTACK_PASSWORD")
    if email and password:
        logger.info("Authenticating with email/password")
        try:
            return SubstackApi(email=email, password=password, publication_url=publication_url)
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
