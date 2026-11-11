"""Shared fixtures.

The real course workbooks are not committed (the repository is public), so the
tests build a synthetic workbook with the same structure as
``small_validation_dataset.xlsx`` (anonymized names). Tests that need the real
files skip themselves when ``data/raw/`` does not contain them.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
W27_PATH = RAW / "scheduling_input_W27.xlsx"
VALIDATION_PATH = RAW / "small_validation_dataset.xlsx"

COMPONENT_COLUMNS = ["active", "component_id", "course_id", "component_type", "section",
                     "parent_component_id", "instructor", "department", "enrollment",
                     "pattern_family", "count_for_np"]
REQUEST_COLUMNS = ["active", "request_id", "target_type", "target_id", "request_type", "days",
                   "start_time", "end_time", "hard", "weight"]
GROUP_COLUMNS = ["active", "group_id", "component_id", "group_weight"]
FIXED_COLUMNS = ["active", "fixed_id", "target_type", "target_id", "days", "start_time",
                 "end_time", "hard"]

# Mirror of small_validation_dataset.xlsx with anonymized names.
TOY_COMPONENTS = [
    ("Y", "C100-LEC", "CRS 100", "LECTURE", "1", None, "Instructor A", "Water", 80, "LEC3", "Y"),
    ("Y", "C100-LAB-01", "CRS 100", "LAB", "1", "C100-LEC", "Instructor A", "Water", 40, "SINGLE_170", "Y"),
    ("Y", "C100-LAB-02", "CRS 100", "LAB", "2", "C100-LEC", "Instructor A", "Water", 40, "SINGLE_170", "Y"),
    ("Y", "C144-LEC", "CRS 144", "LECTURE", "1", None, "Instructor B", "Water", 75, "LEC3", "Y"),
    ("Y", "C144-LAB-01", "CRS 144", "LAB", "1", "C144-LEC", "Instructor B", "Water", 75, "SINGLE_110", "Y"),
    ("Y", "C146-LEC", "CRS 146", "LECTURE", "1", None, "Instructor C", "Water", 49, "LEC3", "Y"),
    ("Y", "C146-DIS-01", "CRS 146", "DISCUSSION", "1", "C146-LEC", "Instructor C", "Water", 49, "SINGLE_50", "Y"),
]
TOY_REQUESTS = [
    ("Y", "REQ-1", "COMPONENT", "C144-LEC", "PREFER_DAYS", "TR", None, None, "N", 1),
]
TOY_GROUPS = [
    ("Y", "LECTURES", "C100-LEC", 1),
    ("Y", "LECTURES", "C144-LEC", 1),
    ("Y", "LECTURES", "C146-LEC", 1),
]
TOY_FIXED = [
    ("Y", "FIX-MWF-AM", "CONFLICT_GROUP", "LECTURES", "MWF", "08:00", "10:00", "Y"),
    ("Y", "FIX-MWF-LATE", "CONFLICT_GROUP", "LECTURES", "MWF", "11:00", "20:00", "Y"),
    ("Y", "FIX-TR-AM", "CONFLICT_GROUP", "LECTURES", "TR", "08:00", "12:30", "Y"),
    ("Y", "FIX-TR-LATE", "CONFLICT_GROUP", "LECTURES", "TR", "14:10", "20:00", "Y"),
]
TOY_SETTINGS = {
    "Quarter": "TOY",  # the validation workbook uses 'Quarter', W27 uses 'term'
    "day_start": "08:00", "day_end": "20:00", "prime_start": "09:00", "prime_end": "16:00",
    "large_enrollment_threshold": 150, "nonprime_rate_large": 0.3, "nonprime_rate_regular": 0.35,
    "overlap_weight": 100, "request_weight": 20, "time_limit_seconds": 60, "mip_gap": 0.0,
}
# All three lectures are forced into MWF 10:00-10:50: 3 overlaps x 100 + REQ-1 violated x 20.
TOY_OPTIMUM = 320.0


def write_workbook(
    path: Path,
    components=TOY_COMPONENTS,
    requests=TOY_REQUESTS,
    groups=TOY_GROUPS,
    settings=TOY_SETTINGS,
    fixed=TOY_FIXED,
) -> Path:
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(components, columns=COMPONENT_COLUMNS).to_excel(writer, sheet_name="Components", index=False)
        pd.DataFrame(requests, columns=REQUEST_COLUMNS).to_excel(writer, sheet_name="Requests", index=False)
        pd.DataFrame(groups, columns=GROUP_COLUMNS).to_excel(writer, sheet_name="ConflictGroups", index=False)
        pd.DataFrame({"key": list(settings), "value": [str(v) for v in settings.values()]}).to_excel(
            writer, sheet_name="Settings", index=False)
        if fixed is not None:
            pd.DataFrame(fixed, columns=FIXED_COLUMNS).to_excel(writer, sheet_name="FixedSchedule", index=False)
    return path


@pytest.fixture
def toy_path(tmp_path: Path) -> Path:
    return write_workbook(tmp_path / "toy.xlsx")


@pytest.fixture
def make_workbook(tmp_path: Path):
    """Factory: make_workbook(components=..., requests=..., ...) -> path."""
    counter = iter(range(1000))

    def _make(**overrides) -> Path:
        return write_workbook(tmp_path / f"wb_{next(counter)}.xlsx", **overrides)

    return _make


requires_w27 = pytest.mark.skipif(not W27_PATH.exists(), reason="data/raw/scheduling_input_W27.xlsx not present")
requires_validation = pytest.mark.skipif(not VALIDATION_PATH.exists(),
                                         reason="data/raw/small_validation_dataset.xlsx not present")
