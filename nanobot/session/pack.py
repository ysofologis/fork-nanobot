"""Session pack key parsing and formatting.

Defines the ``SessionPackKey`` dataclass and ``parse_session_key`` /
``format_session_key`` helpers for the ``channel:session-name#NN``
convention.

Examples
--------
>>> parse_session_key("assistant:nanobot-features#01")
SessionPackKey(channel='assistant', session_name='nanobot-features', index=1)

>>> parse_session_key("nanobot-features#01")
SessionPackKey(channel=None, session_name='nanobot-features', index=1)

>>> parse_session_key("assistant:nanobot-features")
SessionPackKey(channel='assistant', session_name='nanobot-features', index=0)

>>> parse_session_key("plain-session")
SessionPackKey(channel=None, session_name='plain-session', index=0)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SESSION_KEY_RE = re.compile(
    r"^(?:(?P<channel>[a-zA-Z0-9_-]+):)?"
    r"(?P<name>[a-zA-Z0-9_/-]+?)"
    r"(?:#(?P<idx>\d+))?$"
)


@dataclass(frozen=True)
class SessionPackKey:
    """Parsed components of a session pack key.

    Attributes
    ----------
    channel:
        Routing channel (e.g. ``"assistant"``).  ``None`` when omitted.
    session_name:
        Topic or feature slug (e.g. ``"nanobot-features"``).
    index:
        Sequential session number parsed from ``#NN`` suffix.
        ``0`` when the suffix is absent.
    """

    channel: str | None
    session_name: str
    index: int = 0


def parse_session_key(key: str) -> SessionPackKey:
    """Parse a session key string into its components.

    Supported formats
    -----------------
    - ``channel:name#NN`` → full pack key
    - ``channel:name`` → pack key without index
    - ``name#NN`` → channel-less pack key
    - ``name`` → plain session (no channel, no index)

    Raises
    ------
    ValueError
        If *key* does not match any known pattern.
    """
    m = _SESSION_KEY_RE.match(key.strip())
    if not m:
        raise ValueError(f"Invalid session key: {key!r}")
    return SessionPackKey(
        channel=m.group("channel"),
        session_name=m.group("name"),
        index=int(m.group("idx")) if m.group("idx") else 0,
    )


def format_session_key(pack: SessionPackKey) -> str:
    """Reverse of :func:`parse_session_key` — compose a key string."""
    prefix = f"{pack.channel}:" if pack.channel else ""
    suffix = f"#{pack.index:02d}" if pack.index else ""
    return f"{prefix}{pack.session_name}{suffix}"


def has_pack_index(key: str) -> bool:
    """Return ``True`` if *key* contains a ``#NN`` pack index suffix."""
    m = _SESSION_KEY_RE.match(key.strip())
    return bool(m and m.group("idx"))
