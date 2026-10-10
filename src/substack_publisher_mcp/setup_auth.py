# ABOUTME: One-time interactive setup: paste a Substack cookie string and store it
# ABOUTME: in the OS keychain, then verify it works against the given publication.

from __future__ import annotations

import getpass
import sys

from substack import Api as SubstackApi

from substack_publisher_mcp.auth import store_cookie_string


def main() -> None:
    print("Substack Publisher MCP — authentication setup\n")
    publication_url = input("Publication URL (e.g. https://you.substack.com): ").strip()
    if not publication_url:
        print("A publication URL is required.")
        sys.exit(1)

    print(
        "\nOpen your publication in a browser where you're logged in, open dev tools "
        "(F12) -> Application/Storage -> Cookies, and copy the full Cookie header value "
        "for the page (or just the 'substack.sid' cookie's value).\n"
        "Paste it below. Input is hidden.\n"
    )
    cookies_string = getpass.getpass("Cookie string: ").strip()
    if not cookies_string:
        print("A cookie string is required.")
        sys.exit(1)

    print("\nVerifying...")
    try:
        client = SubstackApi(cookies_string=cookies_string, publication_url=publication_url)
        profile = client.get_user_profile()
        name = profile.get("name") or profile.get("email") or "unknown user"
    except Exception as e:
        print(f"Verification failed: {e}")
        print("Double-check the cookie string and publication URL, then try again.")
        sys.exit(1)

    store_cookie_string(publication_url, cookies_string)
    print(f"Authenticated as {name}. Credentials stored securely for {publication_url}.")
    print(
        "\nAdd this to your MCP client config:\n"
        '  "substack-publisher-mcp": {\n'
        '    "command": "substack-publisher-mcp",\n'
        '    "env": { "SUBSTACK_PUBLICATION_URL": "%s" }\n'
        "  }" % publication_url
    )


if __name__ == "__main__":
    main()
