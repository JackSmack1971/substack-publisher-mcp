# ABOUTME: One-time interactive setup: validate a Substack session cookie and store it in the OS keychain.
# ABOUTME: Cookie input is hidden and is never included in setup diagnostics.

from __future__ import annotations

import getpass
import sys

import requests

from substack_publisher_mcp.auth import store_cookie_string
from substack_publisher_mcp.live_verify import BoundaryError, _publication, restricted_client


def _cookie_string(value: str) -> str:
    """Accept a bare substack.sid value or exactly one substack.sid pair."""
    value = value.strip()
    if not value:
        raise ValueError("empty")
    if value.lower().startswith("cookie:") or "\r" in value or "\n" in value:
        raise ValueError("header")
    if value.startswith("substack.sid="):
        cookie_value = value.partition("=")[2]
    elif "=" not in value:
        cookie_value = value
    else:
        raise ValueError("format")
    if not cookie_value or any(
        ord(char) < 0x21 or ord(char) > 0x7E or char in ';,"\\'
        for char in cookie_value
    ):
        raise ValueError("format")
    return f"substack.sid={cookie_value}"


def _has_authenticated_profile(profile: object) -> bool:
    """Require the user identifier returned by python-substack's profile API."""
    if not isinstance(profile, dict):
        return False
    profile_id = profile.get("id")
    return isinstance(profile_id, int) and not isinstance(profile_id, bool) and profile_id > 0


def _diagnostic(
    exc: Exception,
    status: int | None,
    host: str | None = None,
    path: str | None = None,
) -> str:
    if isinstance(exc, BoundaryError):
        host = host or exc.host
        path = path or exc.path
    else:
        host = host or getattr(exc, "http_host", None)
        path = path or getattr(exc, "http_path", None)
    # Only show known setup endpoints; never print response URLs, query strings,
    # redirect destinations, headers, bodies, or provider exception messages.
    safe_hosts = {"substack.com", "brettousignant.substack.com"}
    safe_paths = {
        "/api/v1/user/profile/self",
        "/sign-in",
        "/api/v1/subscriptions",
    }
    location = (
        f" at {host}{path}"
        if host in safe_hosts and path in safe_paths
        else ""
    )
    if status in {301, 302, 303, 307, 308}:
        return (
            f"Authentication check was redirected{location} (HTTP {status}; "
            "classification: REDIRECT, authentication result inconclusive). "
            "No redirect was followed and no cookie was stored."
        )
    if isinstance(exc, BoundaryError):
        return f"Restricted request boundary blocked the check{location} (classification: BLOCKED)."
    if status == 401:
        return f"Substack returned HTTP 401{location} (classification: AUTH_FAILURE)."
    if status == 403:
        return f"Substack returned HTTP 403{location} (classification: PERMISSION_DENIED)."
    if status in {400, 404, 405}:
        return f"Substack returned HTTP {status}{location} (classification: ENDPOINT_DRIFT)."
    if isinstance(exc, requests.Timeout):
        return f"The check timed out{location} (classification: TRANSIENT_ERROR)."
    if isinstance(exc, requests.ConnectionError):
        return f"Could not connect to Substack{location} (classification: TRANSIENT_ERROR)."
    if status is not None and 500 <= status:
        return f"Substack returned HTTP {status}{location} (classification: TRANSIENT_ERROR)."
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return f"Substack returned an unexpected response{location} (classification: RESPONSE_CONTRACT_MISMATCH)."
    return f"Session check failed{location} (classification: UNKNOWN)."


def main() -> None:
    if "--diagnose-redirect" in sys.argv[1:]:
        diagnose_redirect()
        return
    print("Substack Publisher MCP — authentication setup\n")
    publication_url = input("Publication URL (https://brettousignant.substack.com): ").strip()
    try:
        _, canonical_url = _publication(publication_url)
    except (ValueError, TypeError):
        print("Enter the publication's plain HTTPS URL, for example https://brettousignant.substack.com (no page path or query).")
        sys.exit(1)
    if canonical_url != "https://brettousignant.substack.com":
        print("This setup is for https://brettousignant.substack.com. Enter that exact publication URL.")
        sys.exit(1)

    print(
        "\nIn Chrome or Edge, open https://brettousignant.substack.com and sign in.\n"
        "Press F12, choose Application (Chrome) or Storage (Edge), then Cookies,\n"
        "and select https://substack.com (or the Substack domain shown there).\n"
        "Find the row named substack.sid and copy only its Value cell. Do not copy\n"
        "the Cookie header or any other cookies. The value is hidden as you type.\n"
    )
    try:
        cookies_string = _cookie_string(getpass.getpass("substack.sid value: "))
    except ValueError:
        print("That does not look like a substack.sid value. Copy only the Value cell (or enter substack.sid=value).")
        sys.exit(1)

    print("\nVerifying the session with the restricted read-only check...")
    client = None
    try:
        with restricted_client(canonical_url, cookies_string) as client:
            profile = client.get_user_profile()
            if not _has_authenticated_profile(profile):
                raise ValueError("unexpected profile response")
    except Exception as exc:
        session = getattr(client, "_session", None)
        status = getattr(exc, "http_status", None) or getattr(session, "current_status", None)
        response = getattr(exc, "response", None)
        status = status or getattr(response, "status_code", None)
        print(
            _diagnostic(
                exc,
                status,
                getattr(session, "current_host", None),
                getattr(session, "current_path", None),
            )
        )
        sys.exit(1)

    try:
        store_cookie_string(canonical_url, cookies_string)
    except Exception:
        print("The session validated, but Windows could not save it to the OS keyring. Check that a Windows Credential Manager keyring is available, then retry.")
        sys.exit(1)
    print(f"Session validated and saved in the OS keyring for {canonical_url}.")
    print(
        "\nAdd this to your MCP client config:\n"
        '  "substack-publisher-mcp": {\n'
        '    "command": "substack-publisher-mcp",\n'
        '    "env": { "SUBSTACK_PUBLICATION_URL": "%s" }\n'
        "  }" % canonical_url
    )


def diagnose_redirect() -> None:
    """Run the separately authorized, read-only two-request redirect probe."""
    print("Substack Publisher MCP — read-only redirect diagnostic\n")
    publication_url = input("Publication URL (https://brettousignant.substack.com): ").strip()
    if publication_url != "https://brettousignant.substack.com":
        print("This diagnostic requires https://brettousignant.substack.com exactly.")
        sys.exit(1)
    try:
        _, canonical_url = _publication(publication_url)
    except (ValueError, TypeError):
        print("Enter the exact publication URL https://brettousignant.substack.com.")
        sys.exit(1)
    if canonical_url != "https://brettousignant.substack.com":
        print("This diagnostic requires https://brettousignant.substack.com exactly.")
        sys.exit(1)
    try:
        cookies_string = _cookie_string(getpass.getpass("Fresh substack.sid value: "))
    except ValueError:
        print("That does not look like a substack.sid value.")
        sys.exit(1)
    try:
        # Api construction performs its bounded profile and publication sign-in
        # requests. Deliberately do not call get_user_profile() here.
        with restricted_client(canonical_url, cookies_string) as client:
            statuses = list(getattr(getattr(client, "_session", None), "statuses", []))
    except Exception as exc:
        status = getattr(exc, "http_status", None)
        if isinstance(exc, BoundaryError):
            status = status or exc.http_status
            if status is not None:
                line = f"HTTP {status}"
                host, path = exc.redirect_host, exc.redirect_path
                if host and path:
                    line += f"; redirect destination {host}{path}"
                print(line)
                return
        status = status or getattr(getattr(exc, "response", None), "status_code", None)
        print(f"HTTP {status}" if status is not None else "Diagnostic failed without a response status.")
        return
    if statuses:
        print("HTTP " + ", HTTP ".join(str(status) for status in statuses))
    else:
        print("Diagnostic completed; no response status was recorded.")


if __name__ == "__main__":
    main()
