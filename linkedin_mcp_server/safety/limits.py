"""Daily and weekly attempt caps for LinkedIn MCP tool calls.

Each guarded tool calls ``await enforce(action_name)`` early in its
wrapper. ``enforce``:

1. Loads the rolling 7-day attempt log from disk
   (``~/.linkedin-mcp/usage.json``, overridable via the
   ``LINKEDIN_MCP_USAGE_FILE`` environment variable).
2. Trims entries older than the largest configured window.
3. Counts attempts of this action in the daily and weekly windows.
4. Raises :class:`SafetyLimitExceeded` if any cap would be hit. The
   record is **not** appended in that case.
5. Otherwise appends the attempt, flushes atomically, and sleeps a
   random jitter (if configured) outside the lock.

Limits are defensive — set well below LinkedIn's published or observed
thresholds. The goal is to absorb a runaway prompt-loop or over-eager
agent before LinkedIn notices, not to maximize throughput. The MCP
exposes no built-in rate limit upstream; this module is that defense.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionLimit:
    """Caps and pacing for one guarded action."""

    daily: int | None = None
    weekly: int | None = None
    jitter_seconds: tuple[float, float] | None = None


# Conservative defaults. LinkedIn's documented invite ceiling is ~100/week;
# 40 leaves comfortable headroom. Reads of own profile / own inbox / own
# feed are unguarded — they are background-traffic shape and rarely
# classified as commercial use.
ACTION_LIMITS: dict[str, ActionLimit] = {
    "connect_with_person": ActionLimit(
        daily=8, weekly=40, jitter_seconds=(30.0, 90.0)
    ),
    "send_message": ActionLimit(daily=20, weekly=80, jitter_seconds=(15.0, 60.0)),
    "get_person_profile": ActionLimit(daily=60, jitter_seconds=(2.0, 8.0)),
    "search_people": ActionLimit(daily=40, jitter_seconds=(2.0, 8.0)),
    "search_companies": ActionLimit(daily=40, jitter_seconds=(2.0, 8.0)),
    "search_jobs": ActionLimit(daily=40, jitter_seconds=(2.0, 8.0)),
    "get_company_employees": ActionLimit(daily=20, jitter_seconds=(3.0, 10.0)),
    "get_company_profile": ActionLimit(daily=40, jitter_seconds=(2.0, 8.0)),
    "get_company_posts": ActionLimit(daily=30, jitter_seconds=(2.0, 8.0)),
    "get_sidebar_profiles": ActionLimit(daily=30, jitter_seconds=(2.0, 8.0)),
    "get_sent_invitations": ActionLimit(daily=20, jitter_seconds=(2.0, 5.0)),
    "get_job_details": ActionLimit(daily=30, jitter_seconds=(2.0, 8.0)),
}

_SECONDS_PER_DAY = 86_400
_SECONDS_PER_WEEK = 7 * _SECONDS_PER_DAY

# Module-level references so tests can monkeypatch them without touching
# the asyncio / random globals used elsewhere.
_sleep = asyncio.sleep
_uniform = random.uniform

_lock = asyncio.Lock()


class SafetyLimitExceeded(RuntimeError):
    """Raised when an attempt would push a counter past its cap."""

    def __init__(self, action: str, window: str, cap: int, used: int) -> None:
        super().__init__(
            f"Safety limit exceeded for '{action}': {used}/{cap} attempts in "
            f"the past {window}. The limit resets as older attempts age out."
        )
        self.action = action
        self.window = window
        self.cap = cap
        self.used = used


def _usage_path() -> Path:
    override = os.environ.get("LINKEDIN_MCP_USAGE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".linkedin-mcp" / "usage.json"


def _load(path: Path) -> dict[str, list[float]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Failed to read usage file %s (%s); starting fresh", path, exc
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[float]] = {}
    for action, timestamps in raw.items():
        if not isinstance(action, str) or not isinstance(timestamps, list):
            continue
        result[action] = [
            float(t) for t in timestamps if isinstance(t, (int, float))
        ]
    return result


def _save(path: Path, data: dict[str, list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def _trim(timestamps: list[float], now: float) -> list[float]:
    cutoff = now - _SECONDS_PER_WEEK
    return [t for t in timestamps if t >= cutoff]


def _count_within(timestamps: list[float], now: float, window_seconds: int) -> int:
    cutoff = now - window_seconds
    return sum(1 for t in timestamps if t >= cutoff)


async def enforce(action: str, *, now: float | None = None) -> None:
    """Check + record + pace one attempt of ``action``.

    Raises :class:`SafetyLimitExceeded` *before* any side effect if a cap
    would be hit; the attempt is not recorded in that case. Otherwise
    records the attempt to disk and applies the configured jitter sleep.

    Actions not present in :data:`ACTION_LIMITS` are unguarded — they
    return immediately. ``now`` is accepted for testing.
    """
    limit = ACTION_LIMITS.get(action)
    if limit is None:
        return

    jitter = None
    async with _lock:
        path = _usage_path()
        data = _load(path)
        current_time = time.time() if now is None else now
        timestamps = _trim(data.get(action, []), current_time)

        if limit.daily is not None:
            used = _count_within(timestamps, current_time, _SECONDS_PER_DAY)
            if used >= limit.daily:
                raise SafetyLimitExceeded(action, "24h", limit.daily, used)

        if limit.weekly is not None:
            used = _count_within(timestamps, current_time, _SECONDS_PER_WEEK)
            if used >= limit.weekly:
                raise SafetyLimitExceeded(action, "7d", limit.weekly, used)

        timestamps.append(current_time)
        data[action] = timestamps
        _save(path, data)

        if limit.jitter_seconds is not None:
            lo, hi = limit.jitter_seconds
            jitter = _uniform(lo, hi)

    if jitter is not None:
        logger.info("safety: sleeping %.1fs before %s returns", jitter, action)
        await _sleep(jitter)
