"""Response renderer for the interactive CLI.

Encapsulates the per-turn logic of streaming deltas, deduplicating
multi-publish copies of the same response content, and flushing any
non-streamed final content to the terminal.

The class owns its own :class:`StreamRenderer` instance and is reset
between user turns.  All callers need to do is:

>>> rr = ResponseRenderer(...)
>>> rr.reset()
>>> await rr.feed(msg, event)
>>> await rr.finalize()

Why a class?  The previous inline implementation in ``commands.py``
grew three pieces of overlapping state (``turn_response``,
``turn_consumed``, ``renderer``) and a 90-line event-dispatch loop.
Pulling it into a single object makes:

* The dedup invariants testable (feed → finalize is idempotent).
* The contract explicit (no implicit ordering of streaming vs printing).
* The CLI loop read top-to-bottom instead of jumping between
  ``_consume_outbound``, ``turn_done.wait()`` and post-wait branches.
"""

from __future__ import annotations

from typing import Any

from nanobot.cli.stream import StreamRenderer


class ResponseRenderer:
    """Render one agent turn end-to-end (stream deltas + final content).

    Single source of truth for the CLI's response-rendering pipeline.
    Owns a :class:`StreamRenderer` for streaming output and tracks
    per-turn dedup state so duplicate publishes (e.g. dual paths in
    ``loop.py`` and ``turn_delivery.py``) never print twice.

    Lifecycle::

        rr = ResponseRenderer(render_markdown=..., bot_name=..., bot_icon=...)
        while True:                          # one iteration per user turn
            rr.reset()                       # fresh stream renderer + state
            # ... agent runs, _consume_outbound calls rr.feed(...) ...
            await turn_done.wait()
            await rr.finalize()              # flush buffered content

    All print paths are idempotent: feeding the same ``msg.content``
    twice is a no-op the second time.  Streaming-vs-direct-print
    decisions are based on whether the renderer ever received a
    :class:`StreamDeltaEvent`; this is handled internally.
    """

    def __init__(
        self,
        *,
        render_markdown: bool = True,
        bot_name: str = "nanobot",
        bot_icon: str = "🐈",
    ) -> None:
        self._render_markdown = render_markdown
        self._bot_name = bot_name
        self._bot_icon = bot_icon
        self._renderer: StreamRenderer | None = None
        # Content that has already been printed/renderer-flushed this turn.
        # Used to skip multi-publish duplicates of the same response.
        self._consumed: set[str] = set()
        # OutboundMessages that arrived before turn_done via the fallthrough
        # path (no event attached).  Drained by ``finalize()``.
        self._buffered: list[Any] = []
        self._done = False
        # Latched: True once any delta was rendered this turn.  Survives
        # ``finalize()`` so callers can still query after the renderer
        # has been released.
        self._streamed = False

    # ------------------------------------------------------------------
    # Public properties — read by the CLI to drive flow control.
    # ------------------------------------------------------------------
    @property
    def done(self) -> bool:
        """True once the turn has produced its terminal output signal."""
        return self._done

    @property
    def streamed(self) -> bool:
        """True if at least one stream delta was rendered.

        Latched for the lifetime of the turn — survives ``finalize()``
        so callers can still query after the renderer is released.
        """
        if self._streamed:
            return True
        return self._renderer.streamed if self._renderer is not None else False

    @property
    def header_printed(self) -> bool:
        """True if the streaming header was printed (used to suppress a
        second header when finalizing)."""
        return (
            self._renderer.header_printed
            if self._renderer is not None
            else False
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Prepare for a fresh turn.

        Creates a new :class:`StreamRenderer` (so each turn starts with a
        clean spinner/Live display) and clears all dedup state.  Safe to
        call once at startup and once per user turn.
        """
        self._renderer = StreamRenderer(
            render_markdown=self._render_markdown,
            bot_name=self._bot_name,
            bot_icon=self._bot_icon,
        )
        self._consumed.clear()
        self._buffered.clear()
        self._done = False
        self._streamed = False

    def stop_for_input(self) -> None:
        """Pause the renderer's spinner so the next user prompt is clean.

        Called by the CLI immediately before reading interactive input.
        A no-op if the renderer has already been closed.
        """
        if self._renderer is not None:
            self._renderer.stop_for_input()

    # ------------------------------------------------------------------
    # Per-message dispatch
    # ------------------------------------------------------------------
    async def feed(self, msg: Any, event: Any | None) -> None:
        """Feed one outbound message through the rendering pipeline.

        ``event`` is the typed event extracted via
        :func:`outbound_event_from_message`, or ``None`` for plain
        outbound messages with no event attached.

        Idempotent on identical ``msg.content`` for non-stream events,
        so callers can pass every duplicate through without bookkeeping.
        """
        # ---- dedup gate -----------------------------------------------
        # Stream events are unique by token position and must never be
        # deduped by content (deltas are intentionally non-unique).
        if (
            msg.content
            and msg.content in self._consumed
            and not isinstance(event, (StreamDeltaEvent, StreamEndEvent))
        ):
            return

        # ---- streaming path -------------------------------------------
        if isinstance(event, StreamDeltaEvent):
            if self._renderer is not None:
                await self._renderer.on_delta(msg.content)
                if self._renderer.streamed:
                    self._streamed = True
            return

        if isinstance(event, StreamEndEvent):
            if self._renderer is not None:
                await self._renderer.on_end(resuming=event.resuming)
            self._done = True
            return

        # ---- suppress chatty non-rendering events ---------------------
        # Progress and retry-wait events would interleave with the Live
        # stream display and garble the output.  The CLI doesn't surface
        # them; the runtime event publisher still records them.
        if isinstance(event, (ProgressEvent, RetryWaitEvent)):
            return

        # ---- full response after streaming finished -------------------
        if isinstance(event, StreamedResponseEvent):
            await self._render_full_response(msg)
            self._done = True
            return

        # ---- plain OutboundMessage (no event) -------------------------
        # Pre-stream completion, buffer for finalize(); afterwards,
        # drop silently (the turn is already complete).
        if self._done:
            return
        if msg.content:
            self._buffered.append(msg)
            self._done = True

    # ------------------------------------------------------------------
    # Post-turn flush
    # ------------------------------------------------------------------
    async def finalize(self) -> None:
        """Render any content that arrived via plain OutboundMessage.

        Called once after ``turn_done`` fires.  Three responsibilities:

        1. Print the first buffered plain message (if any) whose content
           hasn't already been consumed — this is the "no event
           attached" fallback path.
        2. Close the StreamRenderer if streaming never produced output,
           so the spinner doesn't leak into the next prompt.
        3. Release the StreamRenderer reference (it's tied to this turn).
        """
        for msg in self._buffered:
            content = msg.content
            meta = getattr(msg, "metadata", None)
            evt = getattr(msg, "event", None)
            if (
                content
                and content not in self._consumed
                and not isinstance(evt, StreamedResponseEvent)
            ):
                self._consumed.add(content)
                if self._renderer is not None:
                    await self._renderer.close()
                # Suppress a duplicate header if the renderer already
                # printed one (e.g. spinner started).
                print_kwargs: dict[str, Any] = {}
                if (
                    self._renderer is not None
                    and self._renderer.header_printed
                ):
                    print_kwargs["show_header"] = False
                _print_agent_response(
                    content,
                    render_markdown=self._render_markdown,
                    metadata=meta,
                    **print_kwargs,
                )
            break  # only the first buffered message ever renders

        # Close renderer if it never streamed — otherwise on_end already
        # flushed the buffer and the spinner was stopped inside it.
        if self._renderer is not None and not self._renderer.streamed:
            await self._renderer.close()
        self._renderer = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _render_full_response(self, msg: Any) -> None:
        """Print a :class:`StreamedResponseEvent` payload if streaming
        didn't happen.

        The ``renderer.streamed`` check decides whether to print the
        full text.  When streaming occurred, the Live display already
        showed the content; we just mark it consumed so a duplicate
        publish doesn't trigger a second print.
        """
        content = msg.content
        if not content:
            return
        if content in self._consumed:
            return
        if self._renderer is None or self._renderer.streamed:
            # Either no renderer (impossible here but defensive) or
            # streaming already rendered — mark consumed and skip.
            self._consumed.add(content)
            return

        self._consumed.add(content)
        await self._renderer.close()
        _print_agent_response(
            content,
            render_markdown=self._render_markdown,
            metadata=getattr(msg, "metadata", None),
        )


# ---------------------------------------------------------------------------
# Late-bound imports to avoid pulling CLI command machinery into lightweight
# modules that only need response rendering.
# ---------------------------------------------------------------------------
def _print_agent_response(
    content: str,
    *,
    render_markdown: bool = True,
    metadata: Any | None = None,
    **kwargs: Any,
) -> None:
    """Render the agent's reply text.  Imported lazily to keep this
    module dependency-light and avoid a circular import on
    ``nanobot.cli.commands``."""
    from nanobot.cli.commands import _print_agent_response as _impl  # noqa: PLC0415

    _impl(content, render_markdown=render_markdown, metadata=metadata, **kwargs)


# Imports placed at bottom so the class is defined before these names are
# referenced — they are referenced inside method bodies via deferred name
# resolution, but keeping them after the class makes the structure obvious.
try:  # pragma: no cover — import-only side effect
    from nanobot.bus.outbound_events import (  # noqa: E402
        ProgressEvent,
        RetryWaitEvent,
        StreamDeltaEvent,
        StreamEndEvent,
        StreamedResponseEvent,
    )
except ImportError:  # pragma: no cover
    # Stub fallbacks so the module can still be imported in environments
    # where the bus events module isn't available (e.g. docs builds).
    class StreamDeltaEvent:  # type: ignore[no-redef]
        pass

    class StreamEndEvent:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.resuming = kwargs.get("resuming", False)

    class StreamedResponseEvent:  # type: ignore[no-redef]
        pass

    class ProgressEvent:  # type: ignore[no-redef]
        pass

    class RetryWaitEvent:  # type: ignore[no-redef]
        pass
