"""Independent feasibility audit of a finished schedule.

The checker works from the cleaned workbook (``InputData``) and a mapping
component -> meeting time. It does not use the optimization model, the
feasible pattern sets or the precomputed overlap indices, and it detects
overlaps by minute-level occupancy rather than interval arithmetic, so a bug
in preprocessing or in the model shows up as a disagreement here.

It can therefore also audit schedules that did not come from the model
(e.g. a hand-made or historical schedule).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import combinations

from .loader import InputData
from .patterns import FAMILY_LAYOUTS, START_TIMES, TimeBlock, ceil_fraction, to_minutes
from .rules import applies, violates


@dataclass
class CheckReport:
    hard_violations: list[str] = field(default_factory=list)
    nonprime: list[dict] = field(default_factory=list)
    group_overlaps: list[tuple[str, str, str, float]] = field(default_factory=list)
    soft_violations: list[tuple[str, str, float]] = field(default_factory=list)
    overlap_penalty: float = 0.0
    request_penalty: float = 0.0

    @property
    def feasible(self) -> bool:
        return not self.hard_violations

    @property
    def objective(self) -> float:
        return self.overlap_penalty + self.request_penalty

    def summary(self) -> str:
        lines = [
            f"Hard-rule violations : {len(self.hard_violations)}",
            f"Group overlaps       : {len(self.group_overlaps)} (penalty {self.overlap_penalty:g})",
            f"Soft violations      : {len(self.soft_violations)} (penalty {self.request_penalty:g})",
            f"Recomputed objective : {self.objective:g}",
        ]
        lines += [f"  ! {v}" for v in self.hard_violations]
        return "\n".join(lines)


def _occupancy(block: TimeBlock) -> set[tuple[str, int]]:
    return {(day, minute) for day in block.days for minute in range(block.start, block.end)}


def _fits_family(family: str, block: TimeBlock) -> bool:
    for days, length in FAMILY_LAYOUTS.get(family, ()):
        starts = {to_minutes(t) for t in START_TIMES[length]}
        if block.days == days and block.end - block.start == length and block.start in starts:
            return True
    return False


def check_schedule(
    inputs: InputData,
    meetings: Mapping[str, TimeBlock],
    *,
    overlap_weight: float | None = None,
    request_weight: float | None = None,
    np_rates: Mapping[str, float] | None = None,
    check_families: bool = True,
) -> CheckReport:
    """Audit every hard rule and recompute the objective for ``meetings`` (component -> block)."""
    W_O = inputs.setting_float("overlap_weight") if overlap_weight is None else overlap_weight
    W_V = inputs.setting_float("request_weight") if request_weight is None else request_weight
    rates = {"regular": inputs.setting_float("nonprime_rate_regular"),
             "large": inputs.setting_float("nonprime_rate_large"), **(np_rates or {})}
    day_start, day_end = inputs.setting_minutes("day_start"), inputs.setting_minutes("day_end")
    prime_start, prime_end = inputs.setting_minutes("prime_start"), inputs.setting_minutes("prime_end")
    threshold = inputs.setting_float("large_enrollment_threshold")
    comps = inputs.components
    report = CheckReport()
    bad = report.hard_violations.append

    # Every component scheduled exactly once, inside the day, in its own family.
    for cid in comps.keys() - meetings.keys():
        bad(f"{cid} has no meeting time")
    for cid in meetings.keys() - comps.keys():
        bad(f"{cid} is scheduled but is not an active component")
    scheduled = {c: m for c, m in meetings.items() if c in comps}
    for cid, block in scheduled.items():
        if block.start < day_start or block.end > day_end:
            bad(f"{cid} meets outside the scheduling day")
        if check_families and not _fits_family(comps[cid].family, block):
            bad(f"{cid} meeting {block.days} {block.start}-{block.end} is not a {comps[cid].family} pattern")

    occ = {cid: _occupancy(block) for cid, block in scheduled.items()}

    # Hard requests.
    for req in inputs.requests:
        for cid, block in scheduled.items():
            if applies(req, comps[cid], inputs.group_members) and violates(req, block):
                if req.hard:
                    bad(f"{cid} violates hard request {req.id} ({req.kind})")
                else:
                    report.soft_violations.append((req.id, cid, req.weight))
                    report.request_penalty += W_V * req.weight

    # Instructor double-booking.
    by_instructor: dict[str, list[str]] = defaultdict(list)
    for cid in scheduled:
        for name in comps[cid].instructors:
            by_instructor[name].append(cid)
    for name, cids in by_instructor.items():
        for a, b in combinations(cids, 2):
            if occ[a] & occ[b]:
                bad(f"instructor {name}: {a} overlaps {b}")

    # Parent/child overlap.
    for cid in scheduled:
        parent = comps[cid].parent_id
        if parent in scheduled and occ[cid] & occ[parent]:
            bad(f"{cid} overlaps its parent {parent}")

    # Non-prime quotas.
    quota: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cid, comp in comps.items():
        if comp.count_for_np:
            quota[(comp.department, "large" if comp.enrollment > threshold else "regular")].append(cid)
    for (dept, size), cids in sorted(quota.items()):
        achieved = sum(
            1 for c in cids
            if c in scheduled and (scheduled[c].start < prime_start or scheduled[c].end > prime_end)
        )
        required = ceil_fraction(rates[size], len(cids))
        report.nonprime.append({"department": dept, "size_class": size, "components": len(cids),
                                "required": required, "achieved": achieved})
        if achieved < required:
            bad(f"non-prime quota {dept}/{size}: {achieved} < {required}")

    # Conflict-group overlaps (penalized, not forbidden).
    for gid, members in inputs.group_members.items():
        weight = inputs.group_weight[gid]
        for a, b in combinations(members, 2):
            if a in occ and b in occ and occ[a] & occ[b]:
                report.group_overlaps.append((gid, a, b, weight))
                report.overlap_penalty += W_O * weight

    return report
