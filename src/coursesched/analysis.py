"""Step 6 - post-optimization analysis: schedule quality, scenarios and sensitivity sweeps.

A ``Scenario`` describes one what-if change to the baseline input (non-prime rates,
Settings overrides, objective weights, requests dropped/added, custom patterns).
``run_scenario`` solves it, optionally breaks ties lexicographically, audits the result
with the independent checker and reports quality metrics. The sweep helpers build lists
of scenarios; ``run_scenarios`` turns them into a table.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Any

import pandas as pd

from .checker import CheckReport, check_schedule
from .loader import InputData
from .model import Solution, build_model, offprime_minutes, solve, tie_break
from .patterns import overlaps
from .preprocess import InfeasibleInputError, ModelData, build_model_data
from .report import meetings_from_assignment
from .rules import Request

EVENING = 18 * 60  # meetings ending after 18:00 count as "evening"
EARLY = 9 * 60  # meetings starting before 09:00 (i.e. at 08:00) count as "early"


@dataclass(frozen=True)
class Scenario:
    name: str
    np_rates: dict[str, float] | None = None
    settings: dict[str, Any] | None = None
    overlap_weight: float | None = None
    request_weight: float | None = None
    drop_requests: tuple[str, ...] = ()
    add_requests: tuple[Request, ...] = ()
    custom_patterns: bool = False
    section_aware_groups: bool = False
    tags: dict[str, Any] = field(default_factory=dict)  # extra columns for result tables


@dataclass
class ScenarioResult:
    scenario: Scenario
    row: dict[str, Any]
    data: ModelData | None = None
    solution: Solution | None = None
    check: CheckReport | None = None


def apply_scenario(inputs: InputData, scenario: Scenario) -> InputData:
    """A copy of ``inputs`` with the scenario's Settings and request changes applied."""
    unknown = set(scenario.drop_requests) - {r.id for r in inputs.requests}
    if unknown:
        raise ValueError(f"Scenario {scenario.name!r} drops unknown requests {sorted(unknown)}")
    return dataclasses.replace(
        inputs,
        settings={**inputs.settings, **(scenario.settings or {})},
        requests=[r for r in inputs.requests if r.id not in scenario.drop_requests]
        + list(scenario.add_requests),
    )


def schedule_metrics(data: ModelData, assignment: dict[str, str]) -> dict[str, Any]:
    """Practical-quality indicators of a schedule (not part of the optimization objective)."""
    prime = data.inputs.setting_minutes("prime_start"), data.inputs.setting_minutes("prime_end")
    meetings = {c: data.patterns[p] for c, p in assignment.items()}
    day_minutes: dict[str, int] = defaultdict(int)
    instructor_day: dict[tuple[str, str], int] = defaultdict(int)
    for cid, pat in meetings.items():
        for day in pat.days:
            day_minutes[day] += pat.duration
            for name in data.components[cid].instructors:
                instructor_day[(name, day)] += pat.duration
    return {
        "offprime_minutes": sum(offprime_minutes(p, *prime) for p in meetings.values()),
        "nonprime_components": sum(p.nonprime for p in meetings.values()),
        "nonprime_required": sum(data.np_required.values()),
        "evening_components": sum(p.end > EVENING for p in meetings.values()),
        "early_8am_components": sum(p.start < EARLY for p in meetings.values()),
        "friday_components": sum("F" in p.days for p in meetings.values()),
        "busiest_day_minutes": max(day_minutes.values(), default=0),
        "quietest_day_minutes": min((day_minutes.get(d, 0) for d in "MTWRF"), default=0),
        "max_instructor_day_minutes": max(instructor_day.values(), default=0),
        "student_conflicts": len(student_conflicts(data, assignment)),
    }


def student_conflicts(data: ModelData, assignment: dict[str, str]) -> list[tuple[str, str, str]]:
    """Conflict-group pairs that no student could take together.

    A student taking a grouped course attends its lecture plus one section of each child
    type (one lab, one discussion, ...). A pair (A, B) is a conflict when every combination
    of A's and B's sections has some clash between the two courses. The base model only
    keeps the lectures A and B apart, so this measures what it does not see.
    """
    meetings = {c: data.patterns[p] for c, p in assignment.items()}
    children: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for cid, comp in data.components.items():
        if comp.parent_id:
            children[comp.parent_id][comp.type].append(cid)

    def options(member: str) -> list[list[str]]:
        return [[member, *combo] for combo in product(*children[member].values())]

    def clash(xs: list[str], ys: list[str]) -> bool:
        return any(overlaps(meetings[x], meetings[y]) for x in xs for y in ys if x in meetings and y in meetings)

    return [(g, a, b) for g, members in data.inputs.group_members.items() for a, b in combinations(members, 2)
            if all(clash(oa, ob) for oa in options(a) for ob in options(b))]


def run_scenario(
    inputs: InputData,
    scenario: Scenario,
    *,
    secondary: str | None = "offprime_minutes",
    solver: str = "auto",
    time_limit: float = 120,
    mip_gap: float = 0.0,
) -> ScenarioResult:
    """Solve one scenario (plus a tie-break stage unless ``secondary`` is None) and audit it."""
    row: dict[str, Any] = {"scenario": scenario.name, **scenario.tags}
    try:
        scenario_inputs = apply_scenario(inputs, scenario)
        data = build_model_data(scenario_inputs, np_rates=scenario.np_rates,
                                custom_patterns=scenario.custom_patterns,
                                section_aware_groups=scenario.section_aware_groups)
    except InfeasibleInputError as exc:
        row.update(status="INFEASIBLE (pre-check)", detail=str(exc))
        return ScenarioResult(scenario, row)

    sm = build_model(data, overlap_weight=scenario.overlap_weight, request_weight=scenario.request_weight)
    kwargs = dict(solver=solver, time_limit=time_limit, mip_gap=mip_gap, verbose=False)
    first = solve(sm, **kwargs)
    final = tie_break(sm, first, secondary=secondary, **kwargs) if secondary and first.has_solution else first
    row.update(status=first.status, solver=first.solver, objective=first.objective, bound=first.bound,
               runtime_s=round(final.runtime, 2))  # a tie-broken solution includes stage 1's time
    if not final.has_solution:
        return ScenarioResult(scenario, row, data, final)

    check = check_schedule(scenario_inputs, meetings_from_assignment(data, final.assignment),
                           overlap_weight=sm.overlap_weight, request_weight=sm.request_weight,
                           np_rates=data.np_rates, section_aware_groups=scenario.section_aware_groups)
    row.update(
        tie_break_status=final.status if final is not first else None,
        checker_feasible=check.feasible,
        checker_objective=check.objective,
        group_overlaps=len(check.group_overlaps),
        overlap_penalty=check.overlap_penalty,
        soft_violations=len(check.soft_violations),
        request_penalty=check.request_penalty,
        **schedule_metrics(data, final.assignment),
    )
    return ScenarioResult(scenario, row, data, final, check)


def run_scenarios(inputs: InputData, scenarios: Iterable[Scenario], *, progress: bool = False,
                  **kwargs) -> tuple[pd.DataFrame, list[ScenarioResult]]:
    results = []
    for scenario in scenarios:
        result = run_scenario(inputs, scenario, **kwargs)
        results.append(result)
        if progress:
            row = result.row
            print(f"    {scenario.name}: {row.get('status')} Z={row.get('objective')} ({row.get('runtime_s')}s)",
                  flush=True)
    return pd.DataFrame([r.row for r in results]), results


# ---- Scenario builders ------------------------------------------------------------------------

def np_rate_scenarios(regular: Sequence[float] = (), large: Sequence[float] = (),
                      base: dict[str, float] | None = None) -> list[Scenario]:
    """Vary one non-prime rate at a time, holding the other at its base value."""
    base = base or {"regular": 0.35, "large": 0.30}
    out = [Scenario(f"regular={r:.2f}", np_rates={**base, "regular": r},
                    tags={"varied": "regular", "rate": r}) for r in regular]
    out += [Scenario(f"large={r:.2f}", np_rates={**base, "large": r},
                     tags={"varied": "large", "rate": r}) for r in large]
    return out


def prime_window_scenarios(windows: Sequence[tuple[str, str]], np_rates: dict[str, float] | None = None
                           ) -> list[Scenario]:
    return [Scenario(f"prime {s}-{e}", np_rates=np_rates, settings={"prime_start": s, "prime_end": e},
                     tags={"prime_start": s, "prime_end": e}) for s, e in windows]


def weight_scenarios(request_weights: Sequence[float], base: Scenario) -> list[Scenario]:
    return [dataclasses.replace(base, name=f"{base.name}, W_V={w:g}", request_weight=w,
                                tags={**base.tags, "request_weight": w}) for w in request_weights]


def hard_request_relaxations(inputs: InputData, base: Scenario) -> list[Scenario]:
    """One scenario per hard request (from the Requests sheet) with that request removed."""
    return [dataclasses.replace(base, name=f"{base.name}, without {r.id}",
                                drop_requests=base.drop_requests + (r.id,),
                                tags={**base.tags, "dropped": r.id, "target": r.target_id, "kind": r.kind})
            for r in inputs.requests
            if r.hard and r.source == "Requests" and r.id not in base.drop_requests]


def w27_v2_scenario(inputs: InputData, base: Scenario | None = None) -> Scenario:
    """Honor two approved W27 requests that the standard pattern families cannot express.

    - REQ-W27-022: ECI 140B accepted a WF 2-4 PM lecture. Encoded as WF 14:10-16:00, the same
      convention the data uses for ECI 212A's approved MW 2-4 PM (REQ-W27-006).
    - REQ-W27-009/010: ECI 281C was approved for MW 3-5 PM. V1 enforces only MW (hard) and
      approximates the time with a soft window; V2 fixes MW 15:00-16:50 instead.
    Both need ``custom_patterns=True`` because LEC4 has no WF block and no 15:00 start.
    """
    base = base or Scenario("baseline")
    add = []
    if "ECI-140B-LEC" in inputs.components:
        add.append(Request("V2-REQ-W27-022", "COMPONENT", "ECI-140B-LEC", "FIXED_PATTERN", "WF",
                           14 * 60 + 10, 16 * 60, True, 0.0, "V2"))
    drop = ()
    if "ECI-281C-LEC" in inputs.components:
        drop = tuple(r.id for r in inputs.requests if r.id in {"REQ-W27-009", "REQ-W27-010"})
        add.append(Request("V2-REQ-W27-009", "COMPONENT", "ECI-281C-LEC", "FIXED_PATTERN", "MW",
                           15 * 60, 16 * 60 + 50, True, 0.0, "V2"))
    return dataclasses.replace(base, name=f"{base.name} + V2 custom patterns", custom_patterns=True,
                               drop_requests=base.drop_requests + drop,
                               add_requests=base.add_requests + tuple(add))


def avoid_day_requests(inputs: InputData, day: str = "F") -> tuple[Request, ...]:
    """Hard AVOID_DAYS requests for every component not pinned by an approved FIXED_PATTERN.

    Used for policy scenarios such as "no Friday classes"; approved fixed meetings are kept.
    """
    pinned = {r.target_id for r in inputs.requests
              if r.hard and r.kind == "FIXED_PATTERN" and r.target_type == "COMPONENT"}
    return tuple(Request(f"AVOID-{day}-{c}", "COMPONENT", c, "AVOID_DAYS", day, None, None, True, 0.0, "scenario")
                 for c in inputs.components if c not in pinned)


# ---- Comparison helpers -------------------------------------------------------------------------

def start_time_profile(data: ModelData, assignments: dict[str, dict[str, str]]) -> pd.DataFrame:
    """Weekly meeting-minutes per start hour, one column per labelled assignment."""
    rows = []
    for label, assignment in assignments.items():
        for pid in assignment.values():
            pat = data.patterns[pid]
            rows.append({"schedule": label, "start_hour": pat.start // 60,
                         "minutes": pat.duration * len(pat.days)})
    table = pd.DataFrame(rows)
    return table.pivot_table(index="start_hour", columns="schedule", values="minutes",
                             aggfunc="sum", fill_value=0).sort_index()


def changed_components(data: ModelData, before: dict[str, str], after: dict[str, str]) -> pd.DataFrame:
    rows = [{"component_id": c, "before": data.patterns[before[c]].label, "after": data.patterns[after[c]].label}
            for c in data.components if before.get(c) != after.get(c)]
    return pd.DataFrame(rows, columns=["component_id", "before", "after"])
