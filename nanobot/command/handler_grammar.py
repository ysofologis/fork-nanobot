"""Parse a `/handler <session-id>` argument into a canonical session key.

The grammar is documented in §7.2 of docs/custom-features-ys-nanobot.md.

Three input shapes are accepted:

1. ``assistant:qna#01`` — canonical, with channel + name + ``#NN`` pack suffix.
2. ``websocket:assistant:qna:01`` — WebUI-side equivalent. The ``websocket:``
   prefix is stripped, and the ``:NN`` pack separator is normalised back
   to ``#NN`` (the URL fragment delimiter ``#`` gets rewritten to ``:`` on
   the WebUI transport).
3. ``qna#01`` — bare form: resolves against the TUI's current channel.

Errors raise :class:`HandlerGrammarError` with a message safe to show to
the user; the grammar module has no I/O and no upstream-nanobot
imports, so it is cheap to unit-test in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Known channel prefixes (subset of the channels upstream uses; the
# session storage layer accepts arbitrary keys, but the /handler command
# rejects unknown channels so typos don't silently create new ones).
KNOWN_CHANNELS: frozenset[str] = frozenset({
    "assistant", "any", "office", "cli", "local", "think", "work",
    "life", "main", "engineer", "hr", "rnd", "bus", "logger",
    "doctor", "interagent", "websocket",
})


class HandlerGrammarError(ValueError):
    """Raised when the /handler argument does not match the grammar."""


@dataclass(frozen=True)
class HandlerTarget:
    """Parsed result of /handler <session-id>.

    Attributes:
        channel: the session channel (e.g. ``assistant``).
        name: the session name (e.g. ``qna``).
        pack: the pack suffix (e.g. ``01``), or empty string if none.
        session_key: the full canonical session key (``channel:name#pack``),
            with pack empty when none was given.
    """

    channel: str
    name: str
    pack: str

    @property
    def session_key(self) -> str:
        if self.pack:
            return f"{self.channel}:{self.name}#{self.pack}"
        return f"{self.channel}:{self.name}"


def _normalise_pack_separator(raw: str) -> str:
    """Replace ``:NN`` pack suffix with ``#NN``.

    The WebUI transport rewrites the URL-fragment delimiter ``#`` to ``:``,
    so a session id coming from the WebUI side uses ``:NN`` instead of
    ``#NN``. We normalise to the canonical ``#NN`` form.

    Only the *last* separator is touched, so a name like ``foo:bar`` is
    not modified (it is a name containing ``:``, not a name with a pack
    suffix — although our grammar does not accept names containing ``:``,
    see ``parse_handler_target``).
    """
    if raw.endswith(":"):
        return raw  # trailing colon with no pack; not a separator to fix
    return raw


def parse_handler_target(raw: str, *, default_channel: str) -> HandlerTarget:
    """Parse a raw /handler argument into a :class:`HandlerTarget`.

    Args:
        raw: the argument as the user typed it (without the leading
            ``/handler``).
        default_channel: channel to use when ``raw`` is the bare form
            (no channel prefix); typically the channel of the current
            TUI session.

    Raises:
        HandlerGrammarError: if the argument cannot be parsed.
    """
    s = raw.strip()
    if not s:
        raise HandlerGrammarError(
            "usage: /handler <session-id>  "
            "(e.g. /handler assistant:qna#01)"
        )

    # Reject bare "websocket" (no colon) before any prefix-stripping — it's
    # a transport prefix, not a channel.
    if s.lower() == "websocket":
        raise HandlerGrammarError(
            "`websocket:` is a transport prefix, not a channel; "
            "drop it or use a real channel"
        )

    # Strip optional "websocket:" prefix.
    if s.lower().startswith("websocket:"):
        s = s[len("websocket:"):]
        if not s:
            raise HandlerGrammarError(
                "websocket: prefix given without a session id"
            )

    # Split on the first ":" to get (channel, rest). If no ":", bare form.
    if ":" not in s:
        channel = default_channel
        rest = s
    else:
        channel, _, rest = s.partition(":")
        channel = channel.strip()
        rest = rest.strip()
        if not rest:
            raise HandlerGrammarError(
                f"missing session name after channel `{channel}`"
            )

    if not channel:
        raise HandlerGrammarError("empty channel")

    if channel == "websocket":
        # 'websocket' is a transport prefix, never a real channel.
        # Catches bare "websocket" (bare-form fallback) and any
        # malformed "websocket:something" where the prefix survived.
        raise HandlerGrammarError(
            "`websocket:` is a transport prefix, not a channel; "
            "drop it or use a real channel"
        )

    if channel not in KNOWN_CHANNELS:
        raise HandlerGrammarError(
            f"unknown channel `{channel}`; "
            f"known channels: {', '.join(sorted(c for c in KNOWN_CHANNELS if c != 'websocket'))}"
        )

    # Now parse rest as <name>[#| :]NN.
    name = rest
    pack = ""

    # Look for pack separator: "#NN" or ":NN" at the end.
    for sep in ("#", ":"):
        idx = rest.rfind(sep)
        if idx == -1:
            continue
        # Only treat as pack separator if everything after the separator
        # is digits. Otherwise the separator is part of the name (e.g.
        # a literal "#" inside the name is not allowed by the grammar
        # but the parser should give a clear error rather than silently
        # accept it).
        candidate_pack = rest[idx + 1:]
        if candidate_pack.isdigit():
            name = rest[:idx]
            pack = candidate_pack
            break
        # else: keep looking for a later separator
    else:
        # No digit pack separator found. If rest ends with ":" or "#"
        # but with non-digit suffix, that's a malformed input.
        if rest.endswith(("#", ":")):
            raise HandlerGrammarError(
                f"pack suffix must be digits after `{rest[-1]}`"
            )

    if not name:
        raise HandlerGrammarError("empty session name")
    if "#" in name or ":" in name:
        raise HandlerGrammarError(
            f"session name `{name}` contains forbidden characters "
            "(`#` or `:`); pick a plain name"
        )

    return HandlerTarget(channel=channel, name=name, pack=pack)
