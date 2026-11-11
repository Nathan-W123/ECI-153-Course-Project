import pytest

from coursesched.loader import Component
from coursesched.patterns import Block
from coursesched.rules import Request, applies, violates


def req(kind, days="", start=None, end=None, target_type="COMPONENT", target_id="X", hard=True):
    return Request("R", target_type, target_id, kind, days, start, end, hard, 1.0)


def comp(cid="X", instructors=("Prof A",)):
    return Component(cid, "CRS 1", "LECTURE", "1", None, "/".join(instructors), instructors,
                     "Dept", 30, "LEC3", True)


MWF_10 = Block("MWF", 600, 650)
TR_1030 = Block("TR", 630, 710)


def test_fixed_pattern():
    assert not violates(req("FIXED_PATTERN", "MWF", 600, 650), MWF_10)
    assert violates(req("FIXED_PATTERN", "MWF", 540, 590), MWF_10)
    assert violates(req("FIXED_PATTERN", "MW", 600, 650), MWF_10)
    assert not violates(req("FIXED_PATTERN", "MWF"), MWF_10)  # days only


def test_unavailable():
    assert violates(req("UNAVAILABLE", "M", 620, 700), MWF_10)
    assert not violates(req("UNAVAILABLE", "TR", 620, 700), MWF_10)
    assert not violates(req("UNAVAILABLE", "MWF", 480, 600), MWF_10)  # block ends as class starts
    assert violates(req("UNAVAILABLE", "", 640, 700), MWF_10)  # no days -> every day
    assert violates(req("UNAVAILABLE", "F"), MWF_10)  # no times -> whole day


def test_prefer_and_avoid_days():
    assert not violates(req("PREFER_DAYS", "TR"), TR_1030)
    assert violates(req("PREFER_DAYS", "TR"), MWF_10)
    assert violates(req("PREFER_DAYS", "MW"), MWF_10)  # Friday is not preferred
    assert violates(req("AVOID_DAYS", "F"), MWF_10)
    assert not violates(req("AVOID_DAYS", "F"), TR_1030)


def test_prefer_time_window():
    assert not violates(req("PREFER_TIME_WINDOW", start=600, end=650), MWF_10)  # boundaries inclusive
    assert violates(req("PREFER_TIME_WINDOW", start=610, end=700), MWF_10)
    assert violates(req("PREFER_TIME_WINDOW", start=540, end=640), MWF_10)
    assert not violates(req("PREFER_TIME_WINDOW", start=540), MWF_10)  # open-ended window


def test_unknown_request_type_raises():
    with pytest.raises(ValueError):
        violates(req("SOMETHING_ELSE"), MWF_10)


def test_applies_by_target_type():
    co_taught = comp("X", ("Prof A", "Prof B"))
    assert applies(req("AVOID_DAYS", "F", target_id="X"), co_taught)
    assert not applies(req("AVOID_DAYS", "F", target_id="Y"), co_taught)
    assert applies(req("AVOID_DAYS", "F", target_type="INSTRUCTOR", target_id="Prof B"), co_taught)
    assert not applies(req("AVOID_DAYS", "F", target_type="INSTRUCTOR", target_id="Prof C"), co_taught)
    groups = {"G1": ["X", "Z"]}
    assert applies(req("UNAVAILABLE", "M", target_type="CONFLICT_GROUP", target_id="G1"), co_taught, groups)
    assert not applies(req("UNAVAILABLE", "M", target_type="CONFLICT_GROUP", target_id="G2"), co_taught, groups)
    with pytest.raises(ValueError):
        applies(req("UNAVAILABLE", target_type="ROOM"), co_taught)
