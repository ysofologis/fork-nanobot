"""Tests for the /prompt launcher and /prompt-list commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import register_builtin_commands
from nanobot.command.router import CommandContext, CommandRouter


def _router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


def _ctx(loop, raw: str, content: str | None = None) -> CommandContext:
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content=content if content is not None else raw,
    )
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=loop)


def _loop(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(workspace=tmp_path)


def _write_prompt(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


class TestPromptCommand:
    @pytest.mark.asyncio
    async def test_launch_loads_prompt_and_returns_none(self, tmp_path: Path) -> None:
        """A valid /prompt launches the prompt body as the turn content."""
        _write_prompt(tmp_path, "my_prompt", "# My Prompt\n\nDo the thing.")
        ctx = _ctx(_loop(tmp_path), "/prompt my_prompt")

        result = await _router().dispatch(ctx)

        assert result is None  # proceeds as an agent turn
        assert ctx.msg.content == "# My Prompt\n\nDo the thing."
        assert ctx.msg.metadata["prompt_launched"] == "my_prompt"

    @pytest.mark.asyncio
    async def test_launch_appends_extra_text(self, tmp_path: Path) -> None:
        _write_prompt(tmp_path, "my_prompt", "# My Prompt")
        ctx = _ctx(_loop(tmp_path), "/prompt my_prompt extra words")

        result = await _router().dispatch(ctx)

        assert result is None
        assert ctx.msg.content == "# My Prompt\n\nextra words"
        assert ctx.msg.metadata["prompt_launched"] == "my_prompt"

    @pytest.mark.asyncio
    async def test_missing_prompt_returns_error(self, tmp_path: Path) -> None:
        ctx = _ctx(_loop(tmp_path), "/prompt nope")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "No prompt named `nope`" in result.content
        assert result.metadata["render_as"] == "text"

    @pytest.mark.asyncio
    async def test_no_args_returns_usage(self, tmp_path: Path) -> None:
        ctx = _ctx(_loop(tmp_path), "/prompt")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "Usage: `/prompt <name>" in result.content

    @pytest.mark.asyncio
    async def test_invalid_name_rejected(self, tmp_path: Path) -> None:
        ctx = _ctx(_loop(tmp_path), "/prompt ../../etc/passwd")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "Invalid prompt name" in result.content

    @pytest.mark.asyncio
    async def test_empty_prompt_file_returns_error(self, tmp_path: Path) -> None:
        _write_prompt(tmp_path, "empty", "")
        ctx = _ctx(_loop(tmp_path), "/prompt empty")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "No prompt named `empty`" in result.content

    @pytest.mark.asyncio
    async def test_launch_is_dispatchable_and_turn_lifecycle(self, tmp_path: Path) -> None:
        router = _router()
        assert router.is_dispatchable_command("/prompt my_prompt") is True
        assert router.is_dispatchable_command("/prompt") is True
        # No-arg /prompt is a side-channel usage; with args it enters the agent path.
        from nanobot.command.builtin import builtin_command_starts_agent_turn

        assert builtin_command_starts_agent_turn("/prompt") is False
        assert builtin_command_starts_agent_turn("/prompt my_prompt") is True


class TestPromptListCommand:
    @pytest.mark.asyncio
    async def test_lists_prompts(self, tmp_path: Path) -> None:
        _write_prompt(tmp_path, "my_prompt", "# My Prompt\n\nbody")
        _write_prompt(tmp_path, "second", "Second prompt")
        ctx = _ctx(_loop(tmp_path), "/prompt-list")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "my_prompt" in result.content
        assert "second" in result.content
        assert "**Prompts** (2)" in result.content

    @pytest.mark.asyncio
    async def test_skips_empty_and_system_prompts(self, tmp_path: Path) -> None:
        _write_prompt(tmp_path, "my_prompt", "x")
        _write_prompt(tmp_path, "blank", "   \n")
        sys_dir = tmp_path / "prompts" / "system_prompts"
        sys_dir.mkdir(parents=True, exist_ok=True)
        (sys_dir / "channel_cli.md").write_text("system", encoding="utf-8")
        ctx = _ctx(_loop(tmp_path), "/prompt-list")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "my_prompt" in result.content
        assert "blank" not in result.content
        assert "channel_cli" not in result.content
        assert "system_prompts" not in result.content

    @pytest.mark.asyncio
    async def test_no_prompts_dir(self, tmp_path: Path) -> None:
        ctx = _ctx(_loop(tmp_path), "/prompt-list")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "No prompts directory" in result.content

    @pytest.mark.asyncio
    async def test_empty_prompts_dir(self, tmp_path: Path) -> None:
        (tmp_path / "prompts").mkdir()
        ctx = _ctx(_loop(tmp_path), "/prompt-list")

        result = await _router().dispatch(ctx)

        assert result is not None
        assert "No launchable prompts found" in result.content
