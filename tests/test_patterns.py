from collections import Counter
from datetime import time

import pytest

from coursesched.patterns import (
    Block,
    ceil_fraction,
    generate_patterns,
    is_nonprime,
    overlaps,
    parse_days,
    to_minutes,
)


def test_pattern_counts_per_family():
    patterns = generate_patterns()
    assert len(patterns) == 231
    assert Counter(p.family for p in patterns.values()) == {
        "LEC3": 20, "LEC4": 36, "SINGLE_50": 60, "SINGLE_80": 40, "SINGLE_110": 30, "SINGLE_170": 45,
    }
    assert all(480 <= p.start and p.end <= 1200 for p in patterns.values())


def test_patterns_respect_day_window():
    patterns = generate_patterns(day_start=9 * 60, day_end=17 * 60)
    assert patterns
    assert all(p.start >= 540 and p.end <= 1020 for p in patterns.values())


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Block("MWF", 540, 590), Block("MW", 570, 680), True),   # tutorial example
        (Block("MWF", 480, 600), Block("MWF", 600, 650), False),  # touching is not overlapping
        (Block("MWF", 600, 650), Block("TR", 600, 680), False),   # different days
        (Block("M", 600, 650), Block("M", 610, 620), True),       # containment
    ],
)
def test_overlaps(a, b, expected):
    assert overlaps(a, b) is expected
    assert overlaps(b, a) is expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (540, 960, False),  # 09:00-16:00 is exactly prime
        (850, 960, False),  # 14:10-16:00 ends at the boundary -> prime
        (480, 530, True),   # starts before 09:00
        (970, 1020, True),  # 16:10 start
        (900, 1010, True),  # runs past 16:00
    ],
)
def test_is_nonprime(start, end, expected):
    assert is_nonprime(start, end, 540, 960) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("08:00", 480), ("8:05", 485), ("16:10:00", 970), (time(9, 30), 570), (0.375, 540),
     (None, None), (float("nan"), None), ("", None)],
)
def test_to_minutes(value, expected):
    assert to_minutes(value) == expected


@pytest.mark.parametrize("bad", ["abc", "25:00", 3])
def test_to_minutes_rejects_garbage(bad):
    with pytest.raises(ValueError):
        to_minutes(bad)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("mwf", "MWF"), ("WM", "MW"), ("Thursday", "R"), ("TTh", "TR"), ("M, W", "MW"), (None, ""),
     (float("nan"), "")],
)
def test_parse_days(value, expected):
    assert parse_days(value) == expected


def test_parse_days_rejects_unknown_codes():
    with pytest.raises(ValueError):
        parse_days("MXZ")


@pytest.mark.parametrize(
    ("rate", "count", "expected"),
    [(0.3, 10, 3), (0.35, 7, 3), (0.35, 20, 7), (0.3, 1, 1), (0.35, 0, 0), (0.35, 12, 5)],
)
def test_ceil_fraction(rate, count, expected):
    assert ceil_fraction(rate, count) == expected
