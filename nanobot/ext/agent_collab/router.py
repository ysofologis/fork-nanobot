"""
Agent-to-Agent Message Router
==============================

Extracted from ``nanobot/agent/loop.py`` so that the agent-routing logic lives
in its own module.  This reduces merge conflicts when upstream changes the agent
loop.

Provides:
- ``|bot-name> content`` syntax parsing via ``AGENT_ROUTING_RE``
- ``parse_agent_route`` — split raw input into (target_agent, content)
- ``forward_to_target_agent`` — publish a message for another agent via the bus
- ``receive_from_agent`` — check the bus for incoming inter-agent messages
- ``route_agent_response`` — send the loop's response back to the originating agent
"""

from __future__ import annotations

import logging
import re

from nanobot.bus.events import InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

# Pattern: |bot-name> rest of the message
AGENT_ROUTING_RE = re.compile(r"^\|([^>]+)>\s*(.*)", re.DOTALL)


def parse_agent_route(raw: str) -> tuple[str | None, str]:
    """Check if *raw* starts with the agent-routing prefix.

    Returns
    -------
    (target_agent, content)
        If the message starts with ``|bot-name>`` the prefix is stripped and
        *target_agent* is set to the extracted name.
    (None, raw)
        If the message is a normal user message with no routing prefix.
    """
    m = AGENT_ROUTING_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, raw


async def forward_to_target_agent(
    bus: object,
    target_agent: str,
    content: str,
    sender: str,
    **extra: object,
) -> OutboundMessage | None:
    """Publish an inter-agent message via the bus.

    Parameters
    ----------
    bus
        A message-bus instance that exposes ``publish_agent_message()``.
    target_agent
        The agent that should receive the message (e.g. ``"bot-analyst"``).
    content
        The message text to forward.
    sender
        Our own agent identity (used by the recipient for return routing).
    **extra
        Additional fields forwarded as ``OutboundMessage`` metadata.

    Returns
    -------
    An ``OutboundMessage`` with ``routed_to_agent=True`` metadata, or *None* if
    the bus does not support agent-to-agent publishing.
    """
    if not hasattr(bus, "publish_agent_message"):
        logger.warning(
            "Bus %s does not support publish_agent_message; "
            "cannot route to agent '%s'",
            type(bus).__name__,
            target_agent,
        )
        return None

    outbound = OutboundMessage(
        content=content,
        sender_id=sender,
        chat_id=f"agent:{target_agent}",
        metadata={
            "target_agent": target_agent,
            "routed_to_agent": True,
            "source_agent": sender,
            **extra,
        },
    )
    await bus.publish_agent_message(outbound)
    logger.info("Routed message to agent '%s' from '%s'", target_agent, sender)
    return outbound


async def receive_from_agent(
    bus: object,
    timeout: float = 0.001,
) -> InboundMessage | None:
    """Try to consume one inter-agent message from the bus.

    Parameters
    ----------
    bus
        A message-bus instance that exposes ``consume_agent_message()`` or an
        ``agent_inbound`` queue.
    timeout
        How long (in seconds) to wait for a message before returning ``None``.

    Returns
    -------
    An ``InboundMessage`` if one is available, otherwise ``None``.
    """
    try:
        if hasattr(bus, "consume_agent_message"):
            import asyncio

            return await asyncio.wait_for(
                bus.consume_agent_message(), timeout=timeout
            )
        if hasattr(bus, "agent_inbound"):
            import asyncio

            queue = bus.agent_inbound
            try:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError:
                return None
    except TimeoutError:
        return None
    except Exception:
        logger.exception("Error receiving agent message")
        return None
    return None


async def route_agent_response(
    bus: object,
    msg: InboundMessage,
    response_text: str,
    agent_id: str,
) -> bool:
    """Send the loop's response back to an agent that originated a request.

    Returns ``True`` if the response was routed (i.e. *msg* was an inter-agent
    message), ``False`` if *msg* was a normal user message.
    """
    sender = getattr(msg, "sender", "") or (msg.metadata or {}).get("source_agent", "")
    if not sender:
        return False

    logger.debug("Routing response back to agent '%s'", sender)
    outbound = OutboundMessage(
        content=response_text,
        sender_id=agent_id,
        chat_id=f"agent:{sender}",
        metadata={"routed_to_agent": True, "target_agent": sender},
    )
    await bus.publish_outbound(outbound)
    return True
