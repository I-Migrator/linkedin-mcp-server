"""Unit tests for the safety/limits module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from linkedin_mcp_server.safety import check
from linkedin_mcp_server.safety.limits import (
    ACTION_LIMITS,
    ActionLimit,
    SafetyLimitExceeded,
    _SECONDS_PER_DAY,
    _SECONDS_PER_WEEK,
    enforce,
)


def _usage_file() -> Path:
    import os

    return Path(os.environ["LINKEDIN_MCP_USAGE_FILE"])


async def test_unguarded_action_returns_immediately() -> None:
    """Actions not in ACTION_LIMITS pass through without recording."""
    await enforce("not_a_real_action")
    assert not _usage_file().exists()


async def test_records_within_cap() -> None:
    """An attempt under the cap is recorded and returns cleanly."""
    await enforce("connect_with_person", now=1_000_000.0)
    data = json.loads(_usage_file().read_text())
    assert data == {"connect_with_person": [1_000_000.0]}


async def test_daily_cap_raises_and_does_not_record(monkeypatch) -> None:
    """Hitting the daily cap raises before appending the new attempt."""
    monkeypatch.setitem(
        ACTION_LIMITS,
        "connect_with_person",
        ActionLimit(daily=2, weekly=10),
    )

    now = 2_000_000.0
    await enforce("connect_with_person", now=now - 100)
    await enforce("connect_with_person", now=now - 50)

    with pytest.raises(SafetyLimitExceeded) as excinfo:
        await enforce("connect_with_person", now=now)

    assert excinfo.value.window == "24h"
    assert excinfo.value.cap == 2
    assert excinfo.value.used == 2

    data = json.loads(_usage_file().read_text())
    # Only the two successful attempts; the rejected one is not appended.
    assert len(data["connect_with_person"]) == 2


async def test_weekly_cap_raises(monkeypatch) -> None:
    """Weekly cap independently rejects even when daily is fine."""
    monkeypatch.setitem(
        ACTION_LIMITS,
        "connect_with_person",
        ActionLimit(daily=100, weekly=2),
    )

    now = 3_000_000.0
    # Two attempts spaced more than a day apart so daily counter is clear
    # but weekly counter is full.
    await enforce("connect_with_person", now=now - 5 * _SECONDS_PER_DAY)
    await enforce("connect_with_person", now=now - 3 * _SECONDS_PER_DAY)

    with pytest.raises(SafetyLimitExceeded) as excinfo:
        await enforce("connect_with_person", now=now)

    assert excinfo.value.window == "7d"


async def test_trim_drops_entries_older_than_a_week(monkeypatch) -> None:
    """Attempts older than the weekly window are dropped on the next call."""
    monkeypatch.setitem(
        ACTION_LIMITS,
        "connect_with_person",
        ActionLimit(daily=10, weekly=20),
    )

    path = _usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = 4_000_000.0
    stale = now - _SECONDS_PER_WEEK - 10
    fresh = now - 100
    path.write_text(json.dumps({"connect_with_person": [stale, fresh]}))

    await enforce("connect_with_person", now=now)

    data = json.loads(path.read_text())
    timestamps = data["connect_with_person"]
    assert stale not in timestamps
    assert fresh in timestamps
    assert now in timestamps


async def test_corrupt_usage_file_starts_fresh() -> None:
    """A malformed usage file does not raise — we just start fresh."""
    path = _usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")

    await enforce("connect_with_person", now=5_000_000.0)

    data = json.loads(path.read_text())
    assert data == {"connect_with_person": [5_000_000.0]}


async def test_jitter_sleep_is_called(monkeypatch) -> None:
    """When jitter is configured, enforce sleeps after recording."""
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("linkedin_mcp_server.safety.limits._sleep", sleep_mock)
    monkeypatch.setitem(
        ACTION_LIMITS,
        "connect_with_person",
        ActionLimit(daily=10, jitter_seconds=(2.0, 5.0)),
    )

    await enforce("connect_with_person", now=6_000_000.0)

    sleep_mock.assert_awaited_once()
    delay = sleep_mock.await_args.args[0]
    assert 2.0 <= delay <= 5.0


async def test_check_wraps_exceeded_as_tool_error(monkeypatch) -> None:
    """The public `check` helper raises ToolError, not SafetyLimitExceeded."""
    from fastmcp.exceptions import ToolError

    monkeypatch.setitem(
        ACTION_LIMITS,
        "connect_with_person",
        ActionLimit(daily=1),
    )

    await check("connect_with_person")

    with pytest.raises(ToolError, match="Safety limit exceeded"):
        await check("connect_with_person")


async def test_concurrent_enforces_serialize(monkeypatch) -> None:
    """The asyncio.Lock prevents two attempts from both seeing 0 used."""
    import asyncio

    monkeypatch.setitem(
        ACTION_LIMITS,
        "connect_with_person",
        ActionLimit(daily=1),
    )

    async def attempt(t: float) -> bool:
        try:
            await enforce("connect_with_person", now=t)
            return True
        except SafetyLimitExceeded:
            return False

    results = await asyncio.gather(attempt(7_000_000.0), attempt(7_000_000.1))
    assert results.count(True) == 1
    assert results.count(False) == 1
