"""Step 1 - read, clean and validate the input workbook.

Sheets
------
Components      (required) one row per schedulable class component
Requests        (required) hard requirements and soft preferences
ConflictGroups  (required) groups of components whose overlap is penalized
Settings        (required) key/value configuration
FixedSchedule   (optional) blocks already occupied by other courses; each active
                row becomes an UNAVAILABLE request for its target
CourseSummary   (optional) course-level totals, used only for cross-checks
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd

from .patterns import FAMILY_LAYOUTS, is_missing, parse_days, to_minutes
from .rules import REQUEST_TYPES, TARGET_TYPES, Request

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "Components": {"active", "component_id", "course_id", "component_type", "parent_component_id",
                   "instructor", "department", "enrollment", "pattern_family"},
    "Requests": {"active", "request_id", "target_type", "target_id", "request_type", "hard", "weight"},
    "ConflictGroups": {"active", "group_id", "component_id", "group_weight"},
    "Settings": {"key", "value"},
}
FIXED_SCHEDULE_COLUMNS = {"active", "fixed_id", "target_type", "target_id", "days",
                          "start_time", "end_time", "hard"}

# Different workbooks use different names for the same setting.
SETTING_ALIASES = {"quarter": "term"}

DEFAULT_SETTINGS: dict[str, Any] = {
    "term": "TERM",
    "day_start": "08:00",
    "day_end": "20:00",
    "prime_start": "09:00",
    "prime_end": "16:00",
    "large_enrollment_threshold": 150,
    "nonprime_rate_large": 0.30,
    "nonprime_rate_regular": 0.35,
    "overlap_weight": 100,
    "request_weight": 20,
    "time_limit_seconds": 300,
    "mip_gap": 0.01,
}

# Validation categories that make the model wrong if ignored.
ERROR_CHECKS = (
    "duplicate_component_ids",
    "duplicate_request_ids",
    "components_without_instructor",
    "missing_parent_component_ids",
    "conflict_components_not_in_components",
    "unknown_pattern_families",
    "unknown_request_types",
    "unknown_target_types",
    "request_targets_not_found",
    "invalid_values",
)
# Validation categories worth reviewing but which do not block the model.
WARNING_CHECKS = (
    "settings_count_mismatch",
    "course_summary_mismatches",
    "soft_requests_without_weight",
)


@dataclass(frozen=True)
class Component:
    id: str
    course_id: str
    type: str
    section: str
    parent_id: str | None
    instructor: str
    instructors: tuple[str, ...]
    department: str
    enrollment: int
    family: str
    count_for_np: bool


@dataclass
class InputData:
    """Cleaned, active-only contents of one input workbook."""

    source: Path
    settings: dict[str, Any]
    components: dict[str, Component]
    requests: list[Request]
    group_members: dict[str, list[str]]
    group_weight: dict[str, float]
    course_summary: pd.DataFrame | None
    validation: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def term(self) -> str:
        return str(self.settings["term"])

    def errors(self) -> dict[str, list[Any]]:
        return {k: self.validation[k] for k in ERROR_CHECKS if self.validation.get(k)}

    def warnings(self) -> dict[str, list[Any]]:
        return {k: self.validation[k] for k in WARNING_CHECKS if self.validation.get(k)}

    def setting_minutes(self, key: str) -> int:
        return to_minutes(self.settings[key])

    def setting_float(self, key: str) -> float:
        return float(self.settings[key])

    def group_pairs(self, section_aware: bool = False) -> list[tuple[str, str, str]]:
        """(group, c, c') pairs whose overlap is penalized.

        Default: every pair of members of each conflict group (the formulation's G_j).
        ``section_aware=True`` (model extension) also pairs each member's labs/discussions
        with the other member and its sections, so a student can take both courses.
        Pairs inside one member's own family are never added: C3 already separates a
        parent from its children, and sibling sections are alternatives.
        """
        children: dict[str, list[str]] = defaultdict(list)
        for cid, comp in self.components.items():
            if comp.parent_id:
                children[comp.parent_id].append(cid)
        pairs: dict[tuple[str, str, str], None] = {}
        for gid, members in self.group_members.items():
            for a, b in combinations(members, 2):
                family_a = [a, *children[a]] if section_aware else [a]
                family_b = [b, *children[b]] if section_aware else [b]
                for u in family_a:
                    for v in family_b:
                        if u != v and (gid, v, u) not in pairs:
                            pairs[(gid, u, v)] = None
        return list(pairs)


def is_yes(value: Any) -> bool:
    return not is_missing(value) and str(value).strip().upper() in {"Y", "YES", "TRUE", "1"}


def split_instructors(text: str) -> tuple[str, ...]:
    """'Bischel/Bombardelli' -> ('Bischel', 'Bombardelli')."""
    return tuple(name.strip() for name in re.split(r"[/,;]+", text) if name.strip())


def _clean_str(value: Any) -> str:
    if is_missing(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _coerce_setting(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if number.is_integer() and "." not in text else number
    return value


def _read_sheet(sheets: dict[str, pd.DataFrame], name: str, required: set[str]) -> list[dict[str, Any]]:
    if name not in sheets:
        raise ValueError(f"Workbook is missing required sheet {name!r}")
    frame = sheets[name]
    frame.columns = [str(c).strip() for c in frame.columns]
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Sheet {name!r} is missing columns {sorted(missing)}")
    return frame.to_dict(orient="records")


def load_workbook(path: str | Path) -> InputData:
    """Read the workbook, keep active rows, normalize values and run validation checks."""
    source = Path(path)
    sheets = pd.read_excel(source, sheet_name=None)
    validation: dict[str, list[Any]] = {k: [] for k in (*ERROR_CHECKS, *WARNING_CHECKS)}

    component_rows = [r for r in _read_sheet(sheets, "Components", REQUIRED_COLUMNS["Components"])
                      if is_yes(r["active"])]
    request_rows = [r for r in _read_sheet(sheets, "Requests", REQUIRED_COLUMNS["Requests"])
                    if is_yes(r["active"])]
    group_rows = [r for r in _read_sheet(sheets, "ConflictGroups", REQUIRED_COLUMNS["ConflictGroups"])
                  if is_yes(r["active"])]
    setting_rows = _read_sheet(sheets, "Settings", REQUIRED_COLUMNS["Settings"])
    fixed_rows = []
    if "FixedSchedule" in sheets:
        fixed_rows = [r for r in _read_sheet(sheets, "FixedSchedule", FIXED_SCHEDULE_COLUMNS)
                      if is_yes(r["active"])]

    # ---- Settings -------------------------------------------------------------------
    settings = dict(DEFAULT_SETTINGS)
    for row in setting_rows:
        key = _clean_str(row["key"])
        if key:
            settings[SETTING_ALIASES.get(key.lower(), key)] = _coerce_setting(row["value"])

    # ---- Components -----------------------------------------------------------------
    components: dict[str, Component] = {}
    id_counts = Counter(_clean_str(r["component_id"]) for r in component_rows)
    validation["duplicate_component_ids"] = sorted(k for k, n in id_counts.items() if n > 1)
    for row in component_rows:
        cid = _clean_str(row["component_id"])
        if cid in components:
            continue  # duplicate already reported; keep the first row
        instructor = _clean_str(row["instructor"])
        if not instructor:
            validation["components_without_instructor"].append(cid)
        family = _clean_str(row["pattern_family"]).upper()
        if family not in FAMILY_LAYOUTS:
            validation["unknown_pattern_families"].append((cid, family))
        try:
            enrollment = int(row["enrollment"])
        except (TypeError, ValueError):
            validation["invalid_values"].append((cid, "enrollment", row["enrollment"]))
            enrollment = 0
        components[cid] = Component(
            id=cid,
            course_id=_clean_str(row["course_id"]),
            type=_clean_str(row["component_type"]).upper(),
            section=_clean_str(row.get("section")),
            parent_id=_clean_str(row["parent_component_id"]) or None,
            instructor=instructor,
            instructors=split_instructors(instructor),
            department=_clean_str(row["department"]),
            enrollment=enrollment,
            family=family,
            count_for_np=True if "count_for_np" not in row else is_yes(row["count_for_np"]),
        )
    validation["missing_parent_component_ids"] = sorted(
        (c.id, c.parent_id) for c in components.values()
        if c.parent_id and c.parent_id not in components
    )

    # ---- Conflict groups ------------------------------------------------------------
    group_members: dict[str, list[str]] = defaultdict(list)
    group_weight: dict[str, float] = {}
    for row in group_rows:
        gid, cid = _clean_str(row["group_id"]), _clean_str(row["component_id"])
        if cid not in components:
            validation["conflict_components_not_in_components"].append((gid, cid))
            continue
        if cid not in group_members[gid]:
            group_members[gid].append(cid)
        if not is_missing(row["group_weight"]):
            group_weight[gid] = float(row["group_weight"])
    for gid in group_members:
        group_weight.setdefault(gid, 1.0)

    # ---- Requests (Requests sheet + FixedSchedule sheet) ----------------------------
    requests: list[Request] = []
    instructors = {name for c in components.values() for name in c.instructors}

    def target_exists(target_type: str, target_id: str) -> bool:
        return {
            "COMPONENT": target_id in components,
            "INSTRUCTOR": target_id in instructors,
            "CONFLICT_GROUP": target_id in group_members,
        }[target_type]

    def add_request(row: dict[str, Any], rid: str, kind: str, weight: Any, source: str) -> None:
        target_type = _clean_str(row["target_type"]).upper()
        target_id = _clean_str(row["target_id"])
        if kind not in REQUEST_TYPES:
            validation["unknown_request_types"].append((rid, kind))
            return
        if target_type not in TARGET_TYPES:
            validation["unknown_target_types"].append((rid, target_type))
            return
        if not target_exists(target_type, target_id):
            validation["request_targets_not_found"].append((rid, target_type, target_id))
            return
        try:
            days = parse_days(row.get("days"))
            start, end = to_minutes(row.get("start_time")), to_minutes(row.get("end_time"))
        except ValueError as exc:
            validation["invalid_values"].append((rid, "days/time", str(exc)))
            return
        if kind in {"PREFER_DAYS", "AVOID_DAYS", "FIXED_PATTERN"} and not days:
            validation["invalid_values"].append((rid, "days", f"{kind} needs days"))
            return
        hard = is_yes(row["hard"])
        weight = 0.0 if is_missing(weight) else float(weight)
        if not hard and weight <= 0:
            validation["soft_requests_without_weight"].append(rid)
        requests.append(Request(rid, target_type, target_id, kind, days, start, end, hard, weight, source))

    validation["duplicate_request_ids"] = sorted(
        k for k, n in Counter(_clean_str(r["request_id"]) for r in request_rows).items() if n > 1
    ) + sorted(k for k, n in Counter(_clean_str(r["fixed_id"]) for r in fixed_rows).items() if n > 1)
    for row in request_rows:
        add_request(row, _clean_str(row["request_id"]), _clean_str(row["request_type"]).upper(),
                    row["weight"], "Requests")
    for row in fixed_rows:
        # A soft fixed block (hard = N) is penalized with weight 1 unless a weight column exists.
        add_request(row, _clean_str(row["fixed_id"]), "UNAVAILABLE", row.get("weight", 1),
                    "FixedSchedule")

    # ---- Cross-checks against CourseSummary and the Settings counts --------------------
    course_summary = sheets.get("CourseSummary")
    has_summary = course_summary is not None and {"course_id", "component_count"} <= set(course_summary.columns)
    per_course = Counter(c.course_id for c in components.values())
    if has_summary:
        for row in course_summary.to_dict(orient="records"):
            course = _clean_str(row["course_id"])
            if per_course.get(course, 0) != int(row["component_count"]):
                validation["course_summary_mismatches"].append(
                    (course, int(row["component_count"]), per_course.get(course, 0)))
    actual_counts = {
        "component_count": len(components),
        # 'base courses' are the CourseSummary rows when present, else distinct course ids.
        "base_course_count": course_summary["course_id"].nunique() if has_summary else len(per_course),
    }
    for key, actual in actual_counts.items():
        expected = settings.get(key)
        if not is_missing(expected) and int(expected) != actual:
            validation["settings_count_mismatch"].append((key, int(expected), actual))

    return InputData(
        source=source.resolve(),
        settings=settings,
        components=components,
        requests=requests,
        group_members=dict(group_members),
        group_weight=group_weight,
        course_summary=course_summary,
        validation=validation,
    )
