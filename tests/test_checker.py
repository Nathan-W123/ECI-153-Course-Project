import pytest

from conftest import TOY_OPTIMUM
from coursesched.checker import check_schedule
from coursesched.loader import load_workbook
from coursesched.patterns import Block

# A hand-built optimal schedule for the toy workbook (lectures forced to MWF 10:00).
OPTIMAL = {
    "C100-LEC": Block("MWF", 600, 650),
    "C100-LAB-01": Block("M", 970, 1140),     # 16:10-19:00 non-prime
    "C100-LAB-02": Block("T", 970, 1140),     # 16:10-19:00 non-prime
    "C144-LEC": Block("MWF", 600, 650),
    "C144-LAB-01": Block("R", 970, 1080),     # 16:10-18:00 non-prime
    "C146-LEC": Block("MWF", 600, 650),
    "C146-DIS-01": Block("F", 540, 590),      # 09:00-09:50 prime
}


@pytest.fixture
def inputs(toy_path):
    return load_workbook(toy_path)


def test_optimal_schedule_passes(inputs):
    report = check_schedule(inputs, OPTIMAL)
    assert report.feasible, report.hard_violations
    assert report.objective == pytest.approx(TOY_OPTIMUM)
    assert len(report.group_overlaps) == 3
    assert report.soft_violations == [("REQ-1", "C144-LEC", 1.0)]
    assert report.nonprime == [{"department": "Water", "size_class": "regular", "components": 7,
                                "required": 3, "achieved": 3}]


def test_detects_instructor_double_booking(inputs):
    meetings = {**OPTIMAL, "C100-LAB-02": Block("M", 1030, 1200)}  # overlaps LAB-01 on Monday
    report = check_schedule(inputs, meetings)
    assert any("instructor Instructor A" in v for v in report.hard_violations)


def test_detects_parent_child_overlap(inputs):
    meetings = {**OPTIMAL, "C146-DIS-01": Block("W", 600, 650)}  # same time as its lecture
    report = check_schedule(inputs, meetings)
    assert any("overlaps its parent C146-LEC" in v for v in report.hard_violations)


def test_detects_hard_request_and_family_violations(inputs):
    meetings = {**OPTIMAL, "C144-LEC": Block("TR", 630, 710)}  # inside FIX-TR-AM block
    report = check_schedule(inputs, meetings)
    assert any("FIX-TR-AM" in v for v in report.hard_violations)
    meetings = {**OPTIMAL, "C144-LEC": Block("MWF", 630, 680)}  # 10:30 start: nonstandard, hits 11:00 block
    report = check_schedule(inputs, meetings)
    assert any("not a LEC3 pattern" in v for v in report.hard_violations)
    assert check_schedule(inputs, meetings, check_families=False).hard_violations == [
        "C144-LEC violates hard request FIX-MWF-LATE (UNAVAILABLE)"]


def test_detects_nonprime_shortfall_and_missing_components(inputs):
    meetings = {**OPTIMAL, "C144-LAB-01": Block("R", 850, 960)}  # 14:10-16:00 is prime
    report = check_schedule(inputs, meetings)
    assert "non-prime quota Water/regular: 2 < 3" in report.hard_violations
    meetings = {c: b for c, b in OPTIMAL.items() if c != "C146-DIS-01"}
    assert "C146-DIS-01 has no meeting time" in check_schedule(inputs, meetings).hard_violations
