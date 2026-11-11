"""Scheduling requests: which components they apply to and which patterns violate them.

Request types
-------------
FIXED_PATTERN       must use exactly the given days (and start/end, when given)
UNAVAILABLE         must not overlap the given window (all days if none given)
PREFER_DAYS         every meeting day must be one of the given days
AVOID_DAYS          no meeting day may be one of the given days
PREFER_TIME_WINDOW  every meeting must lie inside [start, end]

Whether a request is a hard constraint or a soft preference is decided by its
``hard`` flag, *not* by its name: the W27 data contains hard PREFER_DAYS and
hard PREFER_TIME_WINDOW requests.

Target types
------------
COMPONENT       one component (target_id = component_id)
INSTRUCTOR      every component the instructor teaches
CONFLICT_GROUP  every member of a conflict group (used by FixedSchedule rows)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .patterns import Block, TimeBlock, overlaps

if TYPE_CHECKING:
    from .loader import Component

REQUEST_TYPES = frozenset(
    {"FIXED_PATTERN", "UNAVAILABLE", "PREFER_DAYS", "AVOID_DAYS", "PREFER_TIME_WINDOW"}
)
TARGET_TYPES = frozenset({"COMPONENT", "INSTRUCTOR", "CONFLICT_GROUP"})

_DAY_START, _DAY_END = 0, 24 * 60


@dataclass(frozen=True)
class Request:
    id: str
    target_type: str
    target_id: str
    kind: str
    days: str
    start: int | None
    end: int | None
    hard: bool
    weight: float
    source: str = "Requests"


def applies(
    request: Request,
    component: Component,
    group_members: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    """True if the request targets this component."""
    if request.target_type == "COMPONENT":
        return request.target_id == component.id
    if request.target_type == "INSTRUCTOR":
        return request.target_id in component.instructors
    if request.target_type == "CONFLICT_GROUP":
        return component.id in (group_members or {}).get(request.target_id, ())
    raise ValueError(f"Unknown target type {request.target_type!r} in request {request.id}")


def violates(request: Request, block: TimeBlock) -> bool:
    """True if scheduling a component at ``block`` violates the request."""
    kind = request.kind
    block_days = set(block.days)

    if kind == "FIXED_PATTERN":
        matches = (
            block.days == request.days
            and (request.start is None or block.start == request.start)
            and (request.end is None or block.end == request.end)
        )
        return not matches

    if kind == "UNAVAILABLE":
        window = Block(
            days=request.days or "MTWRF",
            start=_DAY_START if request.start is None else request.start,
            end=_DAY_END if request.end is None else request.end,
        )
        return overlaps(block, window)

    if kind == "PREFER_DAYS":
        return not block_days <= set(request.days)

    if kind == "AVOID_DAYS":
        return bool(block_days & set(request.days))

    if kind == "PREFER_TIME_WINDOW":
        start = _DAY_START if request.start is None else request.start
        end = _DAY_END if request.end is None else request.end
        return block.start < start or block.end > end

    raise ValueError(f"Unknown request type {kind!r} in request {request.id}")
