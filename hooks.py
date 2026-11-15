# ruff: noqa: INP001
"""Hook entrypoint for the MindRoom pull-requests plugin."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mindroom.plugin_api import hook

from .state import ThreadLink, find_pull_request, read_thread_links, write_thread_link

if TYPE_CHECKING:
    from mindroom.plugin_api import ToolAfterCallContext

_SHELL_TOOL = "run_shell_command"
_GITHUB_TOOL = "create_pull_request"
_CREATE_COMMAND = "gh pr create"


def _creates_pull_request(ctx: ToolAfterCallContext) -> bool:
    if ctx.tool_name == _GITHUB_TOOL:
        return True
    if ctx.tool_name != _SHELL_TOOL:
        return False
    command = ctx.arguments.get("args")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return isinstance(command, str) and _CREATE_COMMAND in command


@hook(event="tool:after_call", name="link-created-pull-request", timeout_ms=5000)
async def link_created_pull_request(ctx: ToolAfterCallContext) -> None:
    """Link the pull request a tool call created to the thread it ran in."""
    if ctx.error is not None or ctx.blocked or ctx.room_id is None or ctx.thread_id is None:
        return
    if ctx.result is None or not _creates_pull_request(ctx):
        return
    pull_request = find_pull_request(str(ctx.result))
    if pull_request is None:
        return
    try:
        links = await read_thread_links(ctx.query_room_state, ctx.room_id, ctx.thread_id)
        if any(link.pull_request == pull_request for link in links):
            return
        link = ThreadLink(pull_request, ctx.agent_name, datetime.now(UTC).isoformat())
        if not await write_thread_link(ctx.put_room_state, ctx.room_id, ctx.thread_id, link):
            ctx.logger.warning("Failed to link pull request", pull_request=pull_request.name)
    except Exception as exc:  # a hook must never break the turn
        ctx.logger.warning("Failed to link pull request", error=str(exc))


__all__ = ["link_created_pull_request"]
