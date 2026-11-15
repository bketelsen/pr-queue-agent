# ruff: noqa: INP001
"""Tests for pull-request state helpers."""

from __future__ import annotations

import asyncio
import sys
from importlib import util
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

PACKAGE_NAME = f"mindroom_plugin_{Path(__file__).resolve().parents[1].name.replace('-', '_')}"
EVENT_TYPE = "com.mindroom.thread.pull_request"


def _load_state_module() -> ModuleType:
    """Load the plugin state module under its synthetic package name."""
    state_path = Path(__file__).resolve().parents[1] / "state.py"
    module_name = f"{PACKAGE_NAME}.state"
    sys.modules.pop(module_name, None)
    spec = util.spec_from_file_location(module_name, state_path)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Mindroom-AI/Mindroom-Chat/pull/412",
        "https://github.com/Mindroom-AI/Mindroom-Chat/pull/412/files",
        "https://github.com/Mindroom-AI/Mindroom-Chat/pull/412/",
        "https://github.com/Mindroom-AI/Mindroom-Chat/pull/412?w=1",
        "https://github.com/Mindroom-AI/Mindroom-Chat/pull/412#discussion_r1",
    ],
)
def test_parse_pull_request_accepts_pull_request_urls(url: str) -> None:
    """Pull request URLs parse to a lower-cased pull request."""
    module = _load_state_module()
    pull_request = module.parse_pull_request(url)
    assert pull_request is not None
    assert pull_request.name == "mindroom-ai/mindroom-chat#412"
    assert pull_request.url == "https://github.com/mindroom-ai/mindroom-chat/pull/412"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/pull/new/branch",
        "https://github.com/o/r/issues/4",
        "https://gitlab.com/o/r/pull/4",
        "http://github.com/o/r/pull/4",
        "github.com/o/r/pull/4",
        "https://github.com/o/r/pull/0",
        "https://github.com/o/r/pull/4x",
        "https://github.com/o/r/pull/4 trailing words",
        "https://github.com/o/r/pull/4\n",
        " https://github.com/o/r/pull/4",
        "https://github.com/o/r/pull/\u0664",
        None,
        4,
    ],
)
def test_parse_pull_request_refuses_other_input(url: object) -> None:
    """Anything but one pull request URL gives None."""
    module = _load_state_module()
    assert module.parse_pull_request(url) is None


def test_find_pull_request_returns_first_pull_request_url() -> None:
    """The first real pull request URL in a text wins."""
    module = _load_state_module()
    text = "remote: https://github.com/o/r/pull/new/x\nhttps://github.com/o/r/pull/7\nhttps://github.com/o/r/pull/8"
    found = module.find_pull_request(text)
    assert found is not None
    assert found.number == 7
    assert module.find_pull_request("nothing here") is None


@pytest.mark.parametrize(
    "text",
    [
        '{"url": "https://github.com/o/r/pull/7", "number": 7}',
        "(https://github.com/o/r/pull/7)",
        "see https://github.com/o/r/pull/7.",
    ],
)
def test_find_pull_request_finds_url_in_json_and_prose(text: str) -> None:
    """A found URL may sit in JSON, parentheses or before punctuation."""
    module = _load_state_module()
    found = module.find_pull_request(text)
    assert found is not None
    assert found.name == "o/r#7"


def test_find_pull_request_skips_url_with_number_suffix() -> None:
    """A number followed by a letter is no pull request."""
    module = _load_state_module()
    found = module.find_pull_request("https://github.com/o/r/pull/4x https://github.com/o/r/pull/9")
    assert found is not None
    assert found.name == "o/r#9"


def test_link_state_key_is_compact_json() -> None:
    """The state key is a compact JSON pair."""
    module = _load_state_module()
    key = module.link_state_key("$root", module.PullRequest("o", "r", 7))
    assert key == '["$root","o/r#7"]'


def _key(thread_id: str, name: str) -> str:
    return f'["{thread_id}","{name}"]'


def test_read_thread_links_filters_and_orders() -> None:
    """Only valid links of the thread come back, ordered by set_at."""
    module = _load_state_module()
    calls: list[tuple[str, str, str | None]] = []
    states: dict[str, Any] = {
        _key("$t", "o/r#2"): {"url": "https://github.com/o/r/pull/2", "set_by": "@a:x", "set_at": "2026-02"},
        _key("$t", "o/r#1"): {"url": "https://github.com/o/r/pull/1", "set_by": "@b:x", "set_at": "2026-01"},
        _key("$t", "o/r#3"): {},
        "not json": {"url": "https://github.com/o/r/pull/9", "set_by": "@a:x", "set_at": "2026-03"},
        _key("$t", "o/r#4"): {"url": "https://example.com/o/r/pull/4", "set_by": "@a:x", "set_at": "2026-04"},
        _key("$t", "o/r#5"): {"url": "https://github.com/o/r/pull/6", "set_by": "@a:x", "set_at": "2026-05"},
        _key("$other", "o/r#7"): {"url": "https://github.com/o/r/pull/7", "set_by": "@a:x", "set_at": "2026-06"},
    }

    async def query(room_id: str, event_type: str, state_key: str | None) -> dict[str, Any] | None:
        calls.append((room_id, event_type, state_key))
        return states

    links = asyncio.run(module.read_thread_links(query, "!room", "$t"))
    assert [link.pull_request.name for link in links] == ["o/r#1", "o/r#2"]
    assert links[0].set_by == "@b:x"
    assert links[0].set_at == "2026-01"
    assert calls == [("!room", EVENT_TYPE, None)]


def test_read_thread_links_returns_empty_on_query_failure() -> None:
    """A failed query gives no links."""
    module = _load_state_module()

    async def query(_room_id: str, _event_type: str, _state_key: str | None) -> dict[str, Any] | None:
        return None

    assert asyncio.run(module.read_thread_links(query, "!room", "$t")) == []


def test_write_and_clear_thread_link() -> None:
    """Write puts the link content, clear puts an empty tombstone."""
    module = _load_state_module()
    puts: list[tuple[str, str, str, dict[str, Any]]] = []

    async def put(room_id: str, event_type: str, state_key: str, content: dict[str, Any]) -> bool:
        puts.append((room_id, event_type, state_key, content))
        return True

    pull_request = module.PullRequest("o", "r", 7)
    link = module.ThreadLink(pull_request, "@a:x", "2026-01")
    assert asyncio.run(module.write_thread_link(put, "!room", "$t", link)) is True
    assert asyncio.run(module.clear_thread_link(put, "!room", "$t", pull_request)) is True
    key = '["$t","o/r#7"]'
    assert puts == [
        ("!room", EVENT_TYPE, key, {"url": "https://github.com/o/r/pull/7", "set_by": "@a:x", "set_at": "2026-01"}),
        ("!room", EVENT_TYPE, key, {}),
    ]
