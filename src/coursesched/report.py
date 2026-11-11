"""Step 6 - turn a solution into tables and an Excel workbook."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .checker import CheckReport
from .model import Solution
from .patterns import Pattern, overlaps, to_clock
from .preprocess import ModelData

_TYPE_ORDER = {"LECTURE": 0, "DISCUSSION": 1, "LAB": 2}


def meetings_from_assignment(data: ModelData, assignment: dict[str, str]) -> dict[str, Pattern]:
    return {c: data.patterns[p] for c, p in assignment.items()}


def schedule_table(data: ModelData, assignment: dict[str, str]) -> pd.DataFrame:
    """One row per component with its assigned meeting time."""
    rows = []
    for cid, comp in data.components.items():
        pattern = data.patterns.get(assignment.get(cid, ""))
        rows.append({
            "component_id": cid,
            "course_id": comp.course_id,
            "component_type": comp.type,
            "section": comp.section,
            "instructor": comp.instructor,
            "department": comp.department,
            "enrollment": comp.enrollment,
            "pattern_family": comp.family,
            "days": pattern.days if pattern else None,
            "start_time": to_clock(pattern.start) if pattern else None,
            "end_time": to_clock(pattern.end) if pattern else None,
            "start_min": pattern.start if pattern else None,
            "end_min": pattern.end if pattern else None,
            "nonprime": pattern.nonprime if pattern else None,
            "feasible_patterns": len(data.P[cid]),
            "pattern_id": pattern.id if pattern else None,
        })
    table = pd.DataFrame(rows)
    table["_type"] = table["component_type"].map(_TYPE_ORDER).fillna(9)
    return (table.sort_values(["department", "course_id", "_type", "section"])
                 .drop(columns="_type").reset_index(drop=True))


def nonprime_table(check: CheckReport) -> pd.DataFrame:
    table = pd.DataFrame(check.nonprime)
    if not table.empty:
        table["achieved_share"] = (table["achieved"] / table["components"]).round(3)
        table["slack"] = table["achieved"] - table["required"]
    return table


def group_overlap_table(data: ModelData, assignment: dict[str, str]) -> pd.DataFrame:
    """Every conflict-group pair: did it overlap, and could it have been avoided at all?"""
    overlapping_options: dict[tuple[str, str, str], int] = {}
    for g, a, b, _, _ in data.C5_indices:
        overlapping_options[(g, a, b)] = overlapping_options.get((g, a, b), 0) + 1
    rows = []
    for g, a, b in data.G:
        pa, pb = data.patterns.get(assignment.get(a, "")), data.patterns.get(assignment.get(b, ""))
        options = data.pair_space.get((a, b), len(data.P[a]) * len(data.P[b]))
        rows.append({
            "group_id": g,
            "component_a": a,
            "component_b": b,
            "group_weight": data.group_weight[g],
            "overlap": bool(pa and pb and overlaps(pa, pb)),
            "meeting_a": pa.label if pa else None,
            "meeting_b": pb.label if pb else None,
            # True when every combination of feasible patterns overlaps.
            "unavoidable": overlapping_options.get((g, a, b), 0) == options,
        })
    return pd.DataFrame(rows)


def soft_request_table(data: ModelData, assignment: dict[str, str]) -> pd.DataFrame:
    rows = []
    for case in data.soft_cases.values():
        pid = assignment.get(case.component_id)
        rows.append({
            "request_id": case.request_id,
            "component_id": case.component_id,
            "weight": case.weight,
            "violated": pid in case.violating,
            "assigned": data.patterns[pid].label if pid else None,
            "violating_options": len(case.violating),
            "feasible_options": len(data.P[case.component_id]),
        })
    return pd.DataFrame(rows)


def summary_table(data: ModelData, solution: Solution, check: CheckReport | None = None) -> pd.DataFrame:
    rows = {
        "term": data.term,
        "input": str(data.inputs.source.name),
        "status": solution.status,
        "objective": solution.objective,
        "best_bound": solution.bound,
        "mip_gap": solution.gap,
        "runtime_s": round(solution.runtime, 3),
        **data.summary(),
        "violated_soft_cases": len(solution.violated_soft_cases),
        "overlapping_group_pairs": len(solution.overlapping_group_pairs),
    }
    if check is not None:
        rows.update({
            "checker_feasible": check.feasible,
            "checker_objective": check.objective,
            "checker_hard_violations": "; ".join(check.hard_violations) or "none",
        })
    return pd.DataFrame({"metric": list(rows), "value": [str(v) for v in rows.values()]})


def export_results(
    out_dir: str | Path,
    data: ModelData,
    solution: Solution,
    check: CheckReport | None = None,
) -> Path:
    """Write <input name>_schedule.xlsx with Summary, Schedule, GroupOverlaps, SoftRequests, NonPrime."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{data.inputs.source.stem}_schedule.xlsx"
    sheets = {
        "Summary": summary_table(data, solution, check),
        "Schedule": schedule_table(data, solution.assignment),
        "GroupOverlaps": group_overlap_table(data, solution.assignment),
        "SoftRequests": soft_request_table(data, solution.assignment),
    }
    if check is not None:
        sheets["NonPrime"] = nonprime_table(check)
    with pd.ExcelWriter(path) as writer:
        for name, table in sheets.items():
            table.to_excel(writer, sheet_name=name, index=False)
    return path
