# ruff: noqa: INP001
"""Pull request links of a thread, backed by Matrix room state."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

THREAD_PULL_REQUEST_EVENT_TYPE = "com.mindroom.thread.pull_request"

type RoomStateQuerier = Callable[[str, str, str | None], Awaitable[dict[str, Any] | None]]
type RoomStatePutter = Callable[[str, str, str, dict[str, Any]], Awaitable[bool]]

_PREFIX = r"https://github\.com/(?P<owner>[A-Za-z0-9-]{1,39})/(?P<repo>[A-Za-z0-9._-]{1,100})/pull/(?P<number>[0-9]{1,10})"
_WHOLE_URL = re.compile(_PREFIX + r"(?:[/?#]\S*)?")
_URL_IN_TEXT = re.compile(_PREFIX + r"(?![A-Za-z0-9_-])")


@dataclass(frozen=True, slots=True)
class PullRequest:
    """One GitHub pull request, with owner and repository in lower case."""

    owner: str
    repo: str
    number: int

    @property
    def url(self) -> str:
        """Return the canonical pull request URL."""
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def name(self) -> str:
        """Return the short name `<owner>/<repo>#<number>`."""
        return f"{self.owner}/{self.repo}#{self.number}"


@dataclass(frozen=True, slots=True)
class ThreadLink:
    """One pull request linked to a thread."""

    pull_request: PullRequest
    set_by: str
    set_at: str


def _from_match(match: re.Match[str]) -> PullRequest | None:
    number = int(match["number"])
    if number < 1:
        return None
    return PullRequest(match["owner"].lower(), match["repo"].lower(), number)


def parse_pull_request(url: object) -> PullRequest | None:
    """Parse a string that is exactly one pull request URL."""
    if not isinstance(url, str):
        return None
    match = _WHOLE_URL.fullmatch(url)
    return _from_match(match) if match else None


def last_pull_request(text: str) -> PullRequest | None:
    """Return the last pull request URL found in a text."""
    found = None
    for match in _URL_IN_TEXT.finditer(text):
        found = _from_match(match) or found
    return found


def link_state_key(thread_id: str, pull_request: PullRequest) -> str:
    """Return the room-state key of one thread and pull request."""
    return json.dumps([thread_id, pull_request.name], separators=(",", ":"))


def _parse_link(thread_id: str, state_key: str, content: object) -> ThreadLink | None:
    if not isinstance(content, Mapping) or not content:
        return None
    pull_request = parse_pull_request(content.get("url"))
    set_by = content.get("set_by")
    set_at = content.get("set_at")
    if pull_request is None or not isinstance(set_by, str) or not isinstance(set_at, str):
        return None
    if state_key != link_state_key(thread_id, pull_request):
        return None
    return ThreadLink(pull_request, set_by, set_at)


async def read_thread_links(
    query_room_state: RoomStateQuerier,
    room_id: str,
    thread_id: str,
) -> list[ThreadLink]:
    """Read the valid pull request links of one thread, in set_at order."""
    states = await query_room_state(room_id, THREAD_PULL_REQUEST_EVENT_TYPE, None)
    if not states:
        return []
    links = [
        link
        for state_key, content in states.items()
        if (link := _parse_link(thread_id, state_key, content)) is not None
    ]
    return sorted(links, key=lambda link: link.set_at)


async def write_thread_link(
    put_room_state: RoomStatePutter,
    room_id: str,
    thread_id: str,
    link: ThreadLink,
) -> bool:
    """Write one pull request link."""
    return await put_room_state(
        room_id,
        THREAD_PULL_REQUEST_EVENT_TYPE,
        link_state_key(thread_id, link.pull_request),
        {"url": link.pull_request.url, "set_by": link.set_by, "set_at": link.set_at},
    )


async def clear_thread_link(
    put_room_state: RoomStatePutter,
    room_id: str,
    thread_id: str,
    pull_request: PullRequest,
) -> bool:
    """Unlink a pull request by writing a tombstone payload."""
    return await put_room_state(
        room_id,
        THREAD_PULL_REQUEST_EVENT_TYPE,
        link_state_key(thread_id, pull_request),
        {},
    )
