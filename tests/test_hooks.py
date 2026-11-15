# ruff: noqa: INP001
"""Tests for the hook that links a pull request a tool call creates."""

from __future__ import annotations

import sys
from importlib import util
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mindroom.hooks.decorators import get_hook_metadata

if TYPE_CHECKING:
    from types import ModuleType

PACKAGE_NAME = f"mindroom_plugin_{Path(__file__).resolve().parents[1].name.replace('-', '_')}"
EVENT_TYPE = "com.mindroom.thread.pull_request"
PR_URL = "https://github.com/o/r/pull/7"


def _load_hooks_module() -> ModuleType:
    """Load the plugin hooks module under its synthetic package name."""
    hooks_path = Path(__file__).resolve().parents[1] / "hooks.py"
    module_name = f"{PACKAGE_NAME}.hooks"
    sys.modules.pop(module_name, None)
    spec = util.spec_from_file_location(module_name, hooks_path)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _context(**overrides: Any) -> SimpleNamespace:  # noqa: ANN401
    """Build a minimal tool:after_call context stub."""
    values: dict[str, Any] = {
        "tool_name": "run_shell_command",
        "arguments": {"args": "gh pr create --fill"},
        "agent_name": "coder",
        "room_id": "!room:localhost",
        "thread_id": "$thread-root",
        "result": f"Created {PR_URL}\n",
        "error": None,
        "blocked": False,
        "query_room_state": AsyncMock(return_value=None),
        "put_room_state": AsyncMock(return_value=True),
        "logger": MagicMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_hook_metadata() -> None:
    """The hook runs after tool calls under its name with a 5 second timeout."""
    module = _load_hooks_module()
    metadata = get_hook_metadata(module.link_created_pull_request)

    assert metadata is not None
    assert metadata.event_name == "tool:after_call"
    assert metadata.hook_name == "link-created-pull-request"
    assert metadata.timeout_ms == 5000


@pytest.mark.asyncio
async def test_shell_command_links_the_created_pull_request() -> None:
    """`gh pr create` in the shell tool links the URL in its output."""
    module = _load_hooks_module()
    ctx = _context()

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_awaited_once()
    room_id, event_type, state_key, content = ctx.put_room_state.await_args.args
    assert room_id == "!room:localhost"
    assert event_type == EVENT_TYPE
    assert state_key == '["$thread-root","o/r#7"]'
    assert content["url"] == PR_URL
    assert content["set_by"] == "coder"
    assert content["set_at"]


@pytest.mark.asyncio
async def test_list_arguments_and_new_branch_url_link_the_real_pull_request() -> None:
    """A command list is joined, and a `pull/new/<branch>` URL is skipped."""
    module = _load_hooks_module()
    ctx = _context(
        arguments={"args": ["bash", "-lc", "git push && gh pr create --fill"]},
        result=f"remote: https://github.com/o/r/pull/new/x\n{PR_URL}\n",
    )

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_awaited_once()
    assert ctx.put_room_state.await_args.args[2] == '["$thread-root","o/r#7"]'


@pytest.mark.asyncio
async def test_shell_command_links_the_last_pull_request_url() -> None:
    """`gh pr create` prints the created URL last, after any URL an earlier command printed."""
    module = _load_hooks_module()
    ctx = _context(
        arguments={"args": "gh pr view 5; gh pr create --fill"},
        result=f"title: old\nurl: https://github.com/o/r/pull/5\n{PR_URL}\n",
    )

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_awaited_once()
    assert ctx.put_room_state.await_args.args[2] == '["$thread-root","o/r#7"]'


@pytest.mark.asyncio
async def test_create_pull_request_tool_links_its_result() -> None:
    """The github toolkit's `create_pull_request` links the URL in its JSON result."""
    module = _load_hooks_module()
    ctx = _context(
        tool_name="create_pull_request",
        arguments={"repo_name": "o/r"},
        result=f'{{"id": 1, "html_url": "{PR_URL}", "number": 7}}',
    )

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_awaited_once()
    assert ctx.put_room_state.await_args.args[2] == '["$thread-root","o/r#7"]'


@pytest.mark.parametrize(
    "overrides",
    [
        {"error": RuntimeError("boom")},
        {"blocked": True},
        {"thread_id": None},
        {"room_id": None},
        {"arguments": {"args": "gh pr view 7"}},
        {"arguments": {}},
        {"result": "Created nothing"},
        {"result": None},
        {"tool_name": "other_tool"},
    ],
)
@pytest.mark.asyncio
async def test_nothing_is_written(overrides: dict[str, Any]) -> None:
    """Failed, blocked, unthreaded or unrelated calls write nothing."""
    module = _load_hooks_module()
    ctx = _context(**overrides)

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_linked_pull_request_is_not_written_again() -> None:
    """A pull request already linked to the thread is left alone."""
    module = _load_hooks_module()
    existing = {
        '["$thread-root","o/r#7"]': {
            "url": PR_URL,
            "set_by": "someone",
            "set_at": "2026-11-15T10:00:00+00:00",
        },
    }
    ctx = _context(query_room_state=AsyncMock(return_value=existing))

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_write_logs_a_warning_and_does_not_raise() -> None:
    """A putter that returns False is logged, not raised."""
    module = _load_hooks_module()
    ctx = _context(put_room_state=AsyncMock(return_value=False))

    await module.link_created_pull_request(ctx)

    ctx.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_unexpected_failure_does_not_raise() -> None:
    """A state read that raises is logged, not raised into the turn."""
    module = _load_hooks_module()
    ctx = _context(query_room_state=AsyncMock(side_effect=RuntimeError("down")))

    await module.link_created_pull_request(ctx)

    ctx.put_room_state.assert_not_awaited()
    ctx.logger.warning.assert_called_once()
