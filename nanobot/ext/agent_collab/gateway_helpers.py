"""
Gateway Helpers
===============

Extracted from ``nanobot/cli/commands.py``.  Provides signal-handler
installation and TTY-mode restoration for the foreground gateway process
so that Ctrl+C works reliably.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from typing import Any, Callable

logger = logging.getLogger(__name__)


def signal_name(signum: int) -> str:
    """Return the human-friendly name of a signal number (e.g. 2 → 'SIGINT')."""
    with suppress(ValueError):
        return signal.Signals(signum).name
    return f"signal {signum}"


def ensure_gateway_tty_signal_mode() -> None:
    """Restore the TTY to a sane signal-handling state.

    This is needed when a raw-mode TTY leak (e.g. from a subprocess or a
    broken terminal library) would otherwise prevent Ctrl+C from being
    delivered to the foreground gateway process.
    """
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    with suppress(Exception):
        import termios

        attrs = termios.tcgetattr(fd)
        lflag = attrs[3]
        required = termios.ISIG | termios.ICANON | termios.ECHO
        if (lflag & required) == required:
            return
        attrs[3] = lflag | required
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        logger.debug("Restored foreground gateway TTY signal mode")


def install_gateway_shutdown_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    tasks: list[asyncio.Task[Any]],
    print_status: Callable[[str], None],
) -> Callable[[], None]:
    """Install foreground gateway signal handlers and return a restore callback.

    Parameters
    ----------
    loop
        The running event loop.
    shutdown_event
        Set when shutdown is requested.
    tasks
        The set of asyncio tasks to cancel on forced shutdown.
    print_status
        Status-line printer for shutdown prompt.

    Returns
    -------
    A zero-argument callable that restores the original handlers.
    """
    loop_signals: list[int] = []
    previous_handlers: list[tuple[int, Any]] = []
    shutdown_requested = False

    def request_shutdown(signum: int) -> None:
        nonlocal shutdown_requested
        sig_name = signal_name(signum)
        if shutdown_requested:
            logger.warning("Forcing gateway shutdown after repeated %s", sig_name)
            for task in tasks:
                if not task.done():
                    task.cancel()
            return
        shutdown_requested = True
        logger.info("Gateway shutdown requested by %s", sig_name)
        print_status("\nShutting down... Press Ctrl+C again to force.")
        shutdown_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, request_shutdown, signum)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, lambda sig, _frame: request_shutdown(sig))
            except (RuntimeError, ValueError):
                logger.debug(
                    "Could not install gateway handler for %s",
                    signal_name(signum),
                )
                continue
            previous_handlers.append((signum, previous))
        else:
            loop_signals.append(signum)

    def restore() -> None:
        for signum in loop_signals:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signum)
        for signum, handler in previous_handlers:
            with suppress(RuntimeError, ValueError):
                signal.signal(signum, handler)

    return restore
