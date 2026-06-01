"""Defensive rate-limit enforcement for LinkedIn MCP tool calls.

Public surface:

    await safety.check("connect_with_person")

Call early in any guarded tool wrapper. Raises ``fastmcp.exceptions.ToolError``
with a clear message if a daily or weekly cap would be exceeded. Otherwise
applies a configured random jitter sleep, records the attempt to a
persistent file, and returns.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from .limits import (
    ACTION_LIMITS,
    SafetyLimitExceeded,
    enforce,
)

__all__ = ["ACTION_LIMITS", "SafetyLimitExceeded", "check", "enforce"]


async def check(action: str) -> None:
    """Enforce a tool's safety limit and surface caps as ToolError to MCP clients."""
    try:
        await enforce(action)
    except SafetyLimitExceeded as exc:
        raise ToolError(str(exc)) from exc
