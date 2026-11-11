"""Meeting patterns: time parsing, pattern generation, overlap and prime-time tests.

A *meeting pattern* is a complete weekly schedule for one component, e.g.
``MWF 09:00-09:50``. Patterns are grouped into *families* (LEC3, LEC4,
SINGLE_50, ...) and every component may only use patterns of its own family.

Time convention: a meeting occupies the half-open interval ``[start, end)``
in minutes after midnight, so a block ending at 10:00 does *not* overlap a
block starting at 10:00.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Protocol

import pandas as pd

DAY_ORDER = "MTWRF"

# Standard start times for each meeting length (minutes).
START_TIMES: dict[int, tuple[str, ...]] = {
    50: ("08:00", "09:00", "10:00", "11:00", "12:10", "13:10",
         "14:10", "15:10", "16:10", "17:10", "18:10", "19:10"),
    80: ("08:00", "09:00", "10:30", "12:10", "13:40", "15:10", "16:40", "18:10"),
    110: ("08:00", "10:00", "12:10", "14:10", "16:10", "18:10"),
    170: ("08:00", "09:00", "11:00", "12:10", "14:10", "15:10", "16:10", "16:30", "17:10"),
}

# Pattern family -> allowed (days, meeting length) layouts.
FAMILY_LAYOUTS: dict[str, tuple[tuple[str, int], ...]] = {
    "LEC3": (("MWF", 50), ("TR", 80)),
    "LEC4": (("MW", 110), ("TR", 110), ("MTWR", 50), ("MTWF", 50)),
    **{f"SINGLE_{length}": tuple((day, length) for day in DAY_ORDER) for length in START_TIMES},
}

_DAY_NAMES = {
    "MONDAY": "M", "MON": "M",
    "TUESDAY": "T", "TUE": "T", "TUES": "T",
    "WEDNESDAY": "W", "WED": "W",
    "THURSDAY": "R", "THURS": "R", "THUR": "R", "THU": "R", "TH": "R",
    "FRIDAY": "F", "FRI": "F",
}


class TimeBlock(Protocol):
    """Anything with ``days`` (e.g. "MWF"), ``start`` and ``end`` (minutes)."""

    days: str
    start: int
    end: int


@dataclass(frozen=True)
class Block:
    """A plain weekly time block (used for requests and external schedules)."""

    days: str
    start: int
    end: int


@dataclass(frozen=True)
class Pattern:
    """A candidate weekly meeting pattern."""

    id: str
    family: str
    days: str
    start: int
    end: int
    nonprime: bool

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def label(self) -> str:
        return f"{self.days} {to_clock(self.start)}-{to_clock(self.end)}"


def is_missing(value: Any) -> bool:
    """True for None, NaN/NaT and blank strings."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def to_minutes(value: Any) -> int | None:
    """Convert '09:30', a time/datetime, or an Excel day fraction to minutes after midnight."""
    if is_missing(value):
        return None
    if isinstance(value, (datetime, time)):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 0 <= value < 1:  # Excel stores times as a fraction of a day
            return round(value * 1440)
        raise ValueError(f"Cannot interpret {value!r} as a clock time")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", str(value).strip())
    if not match:
        raise ValueError(f"Cannot interpret {value!r} as a clock time (expected HH:MM)")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 24 or minutes > 59:
        raise ValueError(f"Invalid clock time {value!r}")
    return hours * 60 + minutes


def to_clock(minutes: int) -> str:
    """Minutes after midnight -> 'HH:MM'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_days(value: Any) -> str:
    """Normalize a day specification to canonical order, e.g. 'wm' -> 'MW', 'Thursday' -> 'R'.

    Returns '' for a missing value. Raises ValueError on unrecognized day codes.
    """
    if is_missing(value):
        return ""
    text = str(value).strip().upper()
    if text in _DAY_NAMES:
        return _DAY_NAMES[text]
    text = re.sub(r"[\s,/;]+", "", text).replace("TH", "R")
    unknown = set(text) - set(DAY_ORDER)
    if unknown:
        raise ValueError(f"Unrecognized day code(s) {sorted(unknown)} in {value!r}")
    return "".join(day for day in DAY_ORDER if day in text)


def overlaps(a: TimeBlock, b: TimeBlock) -> bool:
    """True if two blocks share a day and their [start, end) intervals intersect."""
    return bool(set(a.days) & set(b.days)) and a.start < b.end and b.start < a.end


def is_nonprime(start: int, end: int, prime_start: int, prime_end: int) -> bool:
    """A meeting is non-prime if any part of it lies outside [prime_start, prime_end]."""
    return start < prime_start or end > prime_end


def pattern_id(family: str, days: str, start: int, end: int) -> str:
    return f"{family}_{days}_{to_clock(start).replace(':', '')}_{to_clock(end).replace(':', '')}"


def generate_patterns(
    day_start: int = 8 * 60,
    day_end: int = 20 * 60,
    prime_start: int = 9 * 60,
    prime_end: int = 16 * 60,
    families: dict[str, tuple[tuple[str, int], ...]] | None = None,
) -> dict[str, Pattern]:
    """Generate every pattern of every family that fits inside the scheduling day."""
    families = FAMILY_LAYOUTS if families is None else families
    patterns: dict[str, Pattern] = {}
    for family, layouts in families.items():
        for days, length in layouts:
            for start_text in START_TIMES[length]:
                start = to_minutes(start_text)
                end = start + length
                if start < day_start or end > day_end:
                    continue
                pid = pattern_id(family, days, start, end)
                patterns[pid] = Pattern(
                    id=pid,
                    family=family,
                    days=days,
                    start=start,
                    end=end,
                    nonprime=is_nonprime(start, end, prime_start, prime_end),
                )
    return patterns


def ceil_fraction(rate: float, count: int) -> int:
    """ceil(rate * count), robust to floating-point noise (e.g. 0.3 * 10 = 3.0000000000000004)."""
    return math.ceil(round(rate * count, 9))
