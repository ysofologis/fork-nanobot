"""Input-raw-mode stdin monitor for Escape (cancel turn) and Ctrl+C (force-exit).

Only *Escape* (``\\x1b``) and *Ctrl+C* (``\\x03``) are recognised; all other
bytes are silently discarded.

.. important::

   Unlike ``tty.setraw()``, this module **preserves OPOST** (output
   processing).  ``tty.setraw()`` clears OPOST, which disables the
   ``\\n`` → ``\\r\\n`` translation and causes output to "stagger"
   rightward while the monitor is active.  This module only sets input
   attributes (no canonical mode, no echo, no signal generation) and
   leaves output flags untouched.

Design
------
A background thread puts the terminal into **input-raw mode** (no line
buffering, no local echo, Ctrl+C is a keypress not SIGINT) while
preserving **output processing** so that ``\\n`` continues to be
translated to ``\\r\\n`` on stdout.  The thread reads stdin byte by
byte. Detected control bytes invoke callbacks, bridging from the thread
back to the async event loop.
"""

from __future__ import annotations

import asyncio
import os
import select
import sys
import threading
from typing import Callable

__all__ = ["watch_control_keys"]

#: How long to sleep between polls when no data is available (seconds).
_POLL_INTERVAL = 0.15

#: Escape byte.
_ESC = b"\x1b"
#: Ctrl+C byte.
_CTRL_C = b"\x03"


def watch_control_keys(
    *,
    on_escape: Callable[[], None] | None = None,
    on_ctrl_c: Callable[[], None] | None = None,
    stop_event: asyncio.Event,
    poll: float = _POLL_INTERVAL,
) -> threading.Thread:
    """Start a background thread that monitors stdin for control keys.

    Parameters
    ----------
    on_escape:
        Called (from the background thread) when Escape is pressed.
    on_ctrl_c:
        Called (from the background thread) when Ctrl+C is pressed.
    stop_event:
        Async event that, when set, causes the monitor thread to exit.
    poll:
        Seconds between polls when no data is ready (default ``0.15``).

    Returns
    -------
    :class:`threading.Thread`
        The started daemon thread.  Call ``.join()`` to wait for shutdown.

    Notes
    -----
    - Puts the terminal into **input-raw mode** (preserving output
      processing) as long as the thread is alive, so the thread **must**
      be stopped (via *stop_event*) before the process exits or the
      terminal will be left in a broken state.

    - The callbacks run **on the background thread**, not on the async loop.
      If a callback needs to interact with asyncio objects (e.g. set an
      asyncio event), it should use ``loop.call_soon_threadsafe``, or push
      onto an ``asyncio.Queue`` that the main loop polls.

    - If stdin is not a TTY the function returns ``None`` immediately.
    """
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return None  # type: ignore[return-value]
    except Exception:
        return None  # type: ignore[reportReturnType]

    if stop_event.is_set():
        return None  # type: ignore[return-value]

    import termios

    # termios sequence indices
    _IFLAG = 0
    _OFLAG = 1
    _CFLAG = 2
    _LFLAG = 3

    def _run() -> None:
        """Background thread body."""
        # Save current attributes so we can restore on exit.
        try:
            attrs = list(termios.tcgetattr(fd))
        except Exception:
            return

        try:
            # Build an "input-raw" config that preserves output processing.
            raw = list(attrs)
            # Input flags: disable special-character processing
            raw[_IFLAG] &= ~(
                termios.BRKINT | termios.ICRNL | termios.IGNBRK
                | termios.IGNCR | termios.INLCR | termios.ISTRIP
                | termios.IXON | termios.PARMRK
            )
            # Local flags: disable canonical mode, echo, signal generation
            raw[_LFLAG] &= ~(
                termios.ECHO | termios.ECHONL | termios.ICANON
                | termios.ISIG | termios.IEXTEN
            )
            # Control flags: set 8-bit characters (leave baud/parity alone)
            raw[_CFLAG] &= ~termios.CSIZE
            raw[_CFLAG] |= termios.CS8
            # Output flags: PRESERVE OPOST so \\n -> \\r\\n still works.
            # tty.setraw() clears OPOST, causing unaligned output.
            termios.tcsetattr(fd, termios.TCSADRAIN, raw)

            poller = select.poll()
            poller.register(fd, select.POLLIN)
            poll_ms = int(poll * 1000)

            while not stop_event.is_set():
                events = poller.poll(poll_ms)
                if not events:
                    continue
                # fd is readable
                try:
                    data = os.read(fd, 64)
                except OSError:
                    break
                if not data:
                    continue  # empty read

                if _ESC in data and on_escape is not None:
                    on_escape()
                if _CTRL_C in data and on_ctrl_c is not None:
                    on_ctrl_c()
        finally:
            # Restore terminal attributes — critical for clean exit.
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
