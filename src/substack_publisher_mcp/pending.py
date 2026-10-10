# ABOUTME: Server-enforced two-step confirmation for destructive actions.
# ABOUTME: Unlike a tool description that merely asks the calling model to wait for
# ABOUTME: confirmation, this requires a server-issued token the model cannot guess
# ABOUTME: or be tricked into fabricating from untrusted content.

from __future__ import annotations

import secrets
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

_TTL_SECONDS = 5 * 60


@dataclass
class PendingAction:
    tool: str
    args: Dict[str, Any]
    summary: str
    issued_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() - self.issued_at > _TTL_SECONDS


class PendingActionStore:
    """In-memory store of actions awaiting confirmation, keyed by a random token."""

    def __init__(self) -> None:
        self._actions: Dict[str, PendingAction] = {}

    def create(self, tool: str, args: Dict[str, Any], summary: str) -> str:
        token = secrets.token_urlsafe(16)
        self._actions[token] = PendingAction(
            tool=tool, args=deepcopy(args), summary=summary
        )
        self._gc()
        return token

    def consume(self, token: str, tool: str) -> PendingAction | None:
        """Look up and remove a pending action. Returns None if missing, expired,
        or issued for a different tool (so a token from one destructive action
        can't be replayed against another)."""
        action = self._actions.pop(token, None)
        if action is None:
            return None
        if action.is_expired():
            return None
        if action.tool != tool:
            return None
        return action

    def _gc(self) -> None:
        expired = [t for t, a in self._actions.items() if a.is_expired()]
        for t in expired:
            self._actions.pop(t, None)


store = PendingActionStore()


def require_confirmation(
    tool_name: str,
    args: Dict[str, Any],
    confirm_token: str | None,
    build_summary: Callable[[], str],
) -> tuple[bool, str]:
    """Returns (is_confirmed, message_or_token).

    If confirm_token is absent: issues a new pending action and returns a message
    showing the summary plus the token the caller must pass back.

    If confirm_token is present: validates it against the store. On success,
    returns (True, ""). On failure, returns (False, error message).
    """
    if not confirm_token:
        summary = build_summary()
        token = store.create(tool_name, args, summary)
        message = (
            f"CONFIRMATION REQUIRED\n\n{summary}\n\n"
            f"To proceed, call this tool again with confirm_token=\"{token}\".\n"
            f"This token is single-use and expires in 5 minutes."
        )
        return False, message

    action = store.consume(confirm_token, tool_name)
    if action is None:
        return False, (
            "That confirmation token is invalid, expired, or already used. "
            "Call the tool again without confirm_token to get a fresh one."
        )
    if action.args != args:
        return False, (
            "The arguments provided don't match the action that was confirmed. "
            "Call the tool again without confirm_token to start over with the new arguments."
        )
    return True, ""
