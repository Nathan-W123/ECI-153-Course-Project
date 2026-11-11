import pytest

from conftest import (
    TOY_COMPONENTS,
    TOY_REQUESTS,
    TOY_SETTINGS,
    VALIDATION_PATH,
    W27_PATH,
    requires_validation,
    requires_w27,
)
from coursesched.loader import load_workbook
from coursesched.preprocess import InfeasibleInputError, build_model_data

LECTURE_SLOT = "LEC3_MWF_1000_1050"


def test_toy_sets_and_parameters(toy_path):
    data = build_model_data(load_workbook(toy_path))
    # FixedSchedule leaves a single lecture slot for all three lectures.
    assert data.pinned_components() == {c: LECTURE_SLOT for c in ("C100-LEC", "C144-LEC", "C146-LEC")}
    assert data.soft_cases["REQ-1@C144-LEC"].violating == (LECTURE_SLOT,)
    assert data.I == [("C100-LAB-01", "C100-LAB-02"), ("C100-LAB-01", "C100-LEC"),
                      ("C100-LAB-02", "C100-LEC"), ("C144-LAB-01", "C144-LEC"), ("C146-DIS-01", "C146-LEC")]
    assert sorted(data.H) == [("C100-LAB-01", "C100-LEC"), ("C100-LAB-02", "C100-LEC"),
                              ("C144-LAB-01", "C144-LEC"), ("C146-DIS-01", "C146-LEC")]
    assert len(data.G) == 3 and len(data.C5_indices) == 3
    assert data.np_required == {("Water", "regular"): 3}  # ceil(0.35 * 7)


def test_overlap_indices_only_contain_overlapping_pairs(toy_path):
    data = build_model_data(load_workbook(toy_path))
    pats = data.patterns
    for a, b, p, q in data.C2_indices + data.C3_indices:
        assert p in data.P[a] and q in data.P[b]
        assert set(pats[p].days) & set(pats[q].days)
        assert pats[p].start < pats[q].end and pats[q].start < pats[p].end


def test_np_rates_override(toy_path):
    data = build_model_data(load_workbook(toy_path), np_rates={"regular": 0.5})
    assert data.np_required == {("Water", "regular"): 4}


def test_hard_request_without_any_matching_pattern_is_reported(make_workbook):
    requests = TOY_REQUESTS + [("Y", "REQ-X", "COMPONENT", "C144-LAB-01", "FIXED_PATTERN", "M",
                                "15:00", "16:50", "Y", 0)]  # 15:00 is not a standard 110-min start
    with pytest.raises(InfeasibleInputError, match="C144-LAB-01.*REQ-X"):
        build_model_data(load_workbook(make_workbook(requests=requests)))


def test_unreachable_nonprime_quota_is_reported(make_workbook):
    # Pin every non-lecture component into prime time; lectures are already pinned prime.
    pins = [("Y", f"PIN-{c[1]}", "COMPONENT", c[1], "PREFER_TIME_WINDOW", None, "09:00", "16:00", "Y", 0)
            for c in TOY_COMPONENTS if c[3] != "LECTURE"]
    with pytest.raises(InfeasibleInputError, match="Non-prime quota"):
        build_model_data(load_workbook(make_workbook(requests=TOY_REQUESTS + pins)))


def test_settings_override_day_window(make_workbook):
    settings = {**TOY_SETTINGS, "day_end": "18:00"}
    data = build_model_data(load_workbook(make_workbook(settings=settings)))
    assert max(p.end for p in data.patterns.values()) <= 18 * 60


@requires_w27
def test_real_w27_model_size():
    data = build_model_data(load_workbook(W27_PATH))
    assert data.summary() == {
        "components": 64, "patterns": 231, "feasible_assignments": 2036, "pinned_components": 8,
        "soft_cases": 4, "instructor_pairs": 53, "parent_child_pairs": 24, "conflict_group_pairs": 117,
        "C2_indices": 5577, "C3_indices": 2651, "C5_indices": 9527,
    }
    assert data.np_required[("Environmental", "large")] == 1  # ceil(0.30 * 1) forces ECI-193-LEC


@requires_validation
def test_real_validation_lectures_are_pinned():
    data = build_model_data(load_workbook(VALIDATION_PATH))
    assert data.pinned_components() == {c: LECTURE_SLOT for c in ("ECI-100-LEC", "ECI-144-LEC", "ECI-146-LEC")}
