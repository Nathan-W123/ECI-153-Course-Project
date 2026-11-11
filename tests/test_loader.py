import pandas as pd
import pytest

from conftest import (
    TOY_COMPONENTS,
    TOY_GROUPS,
    TOY_REQUESTS,
    TOY_SETTINGS,
    VALIDATION_PATH,
    W27_PATH,
    requires_validation,
    requires_w27,
)
from coursesched.loader import load_workbook
from coursesched.preprocess import InputValidationError, build_model_data


def test_toy_workbook_loads_cleanly(toy_path):
    inputs = load_workbook(toy_path)
    assert inputs.term == "TOY"  # 'Quarter' is normalized to 'term'
    assert len(inputs.components) == 7
    assert inputs.errors() == {} and inputs.warnings() == {}
    assert inputs.group_members == {"LECTURES": ["C100-LEC", "C144-LEC", "C146-LEC"]}
    fixed = [r for r in inputs.requests if r.source == "FixedSchedule"]
    assert len(fixed) == 4 and all(r.kind == "UNAVAILABLE" and r.hard for r in fixed)
    assert inputs.setting_minutes("prime_end") == 960
    assert inputs.setting_float("mip_gap") == 0.0


def test_inactive_rows_are_dropped(make_workbook):
    components = TOY_COMPONENTS + [("N", "OFF-LEC", "CRS 9", "LECTURE", "1", None, "Instructor Z",
                                    "Water", 10, "LEC3", "Y")]
    requests = TOY_REQUESTS + [("N", "REQ-OFF", "COMPONENT", "OFF-LEC", "PREFER_DAYS", "TR", None,
                                None, "N", 1)]
    inputs = load_workbook(make_workbook(components=components, requests=requests))
    assert "OFF-LEC" not in inputs.components
    assert [r.id for r in inputs.requests if r.source == "Requests"] == ["REQ-1"]


def test_co_taught_instructors_are_split(make_workbook):
    components = TOY_COMPONENTS[:-1] + [("Y", "C146-DIS-01", "CRS 146", "DISCUSSION", "1", "C146-LEC",
                                         "Instructor C/Instructor A", "Water", 49, "SINGLE_50", "Y")]
    inputs = load_workbook(make_workbook(components=components))
    assert inputs.components["C146-DIS-01"].instructors == ("Instructor C", "Instructor A")


def test_validation_flags_data_problems(make_workbook):
    components = TOY_COMPONENTS + [
        TOY_COMPONENTS[0],  # duplicate id
        ("Y", "BAD-1", "CRS 9", "LAB", "1", "NO-SUCH-PARENT", "Instructor Z", "Water", 10, "SINGLE_50", "Y"),
        ("Y", "BAD-2", "CRS 9", "LAB", "2", None, None, "Water", 10, "SINGLE_50", "Y"),
        ("Y", "BAD-3", "CRS 9", "LAB", "3", None, "Instructor Z", "Water", 10, "LEC9", "Y"),
    ]
    requests = TOY_REQUESTS + [
        ("Y", "REQ-1", "COMPONENT", "C100-LEC", "AVOID_DAYS", "F", None, None, "N", 1),  # duplicate id
        ("Y", "REQ-2", "COMPONENT", "C100-LEC", "TELEPORT", "F", None, None, "N", 1),
        ("Y", "REQ-3", "COMPONENT", "GHOST", "AVOID_DAYS", "F", None, None, "N", 1),
        ("Y", "REQ-4", "ROOM", "C100-LEC", "AVOID_DAYS", "F", None, None, "N", 1),
        ("Y", "REQ-5", "COMPONENT", "C100-LEC", "UNAVAILABLE", "M", "noon", None, "Y", 0),
        ("Y", "REQ-6", "COMPONENT", "C100-LEC", "AVOID_DAYS", "F", None, None, "N", 0),
    ]
    groups = TOY_GROUPS + [("Y", "LECTURES", "GHOST", 1)]
    inputs = load_workbook(make_workbook(components=components, requests=requests, groups=groups))
    v = inputs.validation
    assert v["duplicate_component_ids"] == ["C100-LEC"]
    assert v["duplicate_request_ids"] == ["REQ-1"]
    assert v["missing_parent_component_ids"] == [("BAD-1", "NO-SUCH-PARENT")]
    assert v["components_without_instructor"] == ["BAD-2"]
    assert v["unknown_pattern_families"] == [("BAD-3", "LEC9")]
    assert v["unknown_request_types"] == [("REQ-2", "TELEPORT")]
    assert v["request_targets_not_found"] == [("REQ-3", "COMPONENT", "GHOST")]
    assert v["unknown_target_types"] == [("REQ-4", "ROOM")]
    assert [item[0] for item in v["invalid_values"]] == ["REQ-5"]
    assert v["conflict_components_not_in_components"] == [("LECTURES", "GHOST")]
    assert v["soft_requests_without_weight"] == ["REQ-6"]
    with pytest.raises(InputValidationError):
        build_model_data(inputs)


def test_missing_sheet_or_column_raises(tmp_path, toy_path):
    with pytest.raises(ValueError, match="ConflictGroups"):
        path = tmp_path / "no_groups.xlsx"
        with pd.ExcelWriter(path) as writer:
            for name in ("Components", "Requests", "Settings"):
                pd.read_excel(toy_path, sheet_name=name).to_excel(writer, sheet_name=name, index=False)
        load_workbook(path)
    with pytest.raises(ValueError, match="instructor"):
        path = tmp_path / "no_instructor.xlsx"
        with pd.ExcelWriter(path) as writer:
            for name, frame in pd.read_excel(toy_path, sheet_name=None).items():
                frame.drop(columns=["instructor"], errors="ignore").to_excel(writer, sheet_name=name, index=False)
        load_workbook(path)


@requires_w27
def test_real_w27_workbook_is_clean():
    inputs = load_workbook(W27_PATH)
    assert inputs.term == "W27"
    assert len(inputs.components) == 64
    assert inputs.errors() == {} and inputs.warnings() == {}
    assert sum(not r.hard for r in inputs.requests) == 4


@requires_validation
def test_real_validation_workbook_is_clean():
    inputs = load_workbook(VALIDATION_PATH)
    assert inputs.term == "W27"
    assert inputs.errors() == {} and inputs.warnings() == {}
    assert sum(r.source == "FixedSchedule" for r in inputs.requests) == 4


def test_settings_counts_are_cross_checked(make_workbook):
    settings = {**TOY_SETTINGS, "component_count": 8, "base_course_count": 3}
    inputs = load_workbook(make_workbook(settings=settings))
    assert inputs.warnings() == {"settings_count_mismatch": [("component_count", 8, 7)]}
    assert inputs.errors() == {}
