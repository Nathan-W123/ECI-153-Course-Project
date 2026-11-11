"""Step 2 - turn cleaned input rows into the sets and parameters of the optimization model.

Notation follows the formulation slides:

    P[c]          feasible patterns of component c (family rules + hard requests)
    soft_cases    one case v per (soft request, component); B_v = violating patterns
    I             pairs of components that share an instructor
    H             (child, parent) component pairs
    G             (group, c, c') pairs inside each conflict group
    C2/C3/C5      (pair, overlapping pattern pair) indices -- the local overlap sets Omega_cc'
    np_sets       C_d^R / C_d^L: components counting toward each department's non-prime quota
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from .loader import Component, InputData
from .patterns import Pattern, ceil_fraction, generate_patterns, overlaps
from .rules import Request, applies, violates

REGULAR, LARGE = "regular", "large"


class InputValidationError(ValueError):
    """The workbook has data problems that would make the model wrong."""


class InfeasibleInputError(ValueError):
    """The hard rules leave no possible schedule (detected before solving)."""


@dataclass(frozen=True)
class SoftCase:
    id: str
    request_id: str
    component_id: str
    weight: float
    violating: tuple[str, ...]  # B_v: feasible patterns of the component that violate the request


@dataclass
class ModelData:
    inputs: InputData
    patterns: dict[str, Pattern]
    P: dict[str, list[str]]
    applicable_requests: dict[str, list[Request]]
    hard_removals: dict[str, dict[str, int]]
    soft_cases: dict[str, SoftCase]
    I: list[tuple[str, str]]
    H: list[tuple[str, str]]
    G: list[tuple[str, str, str]]
    C2_indices: list[tuple[str, str, str, str]]
    C3_indices: list[tuple[str, str, str, str]]
    C5_indices: list[tuple[str, str, str, str, str]]
    np_sets: dict[tuple[str, str], list[str]]
    np_required: dict[tuple[str, str], int]
    np_rates: dict[str, float]
    pair_space: dict[tuple[str, str], int] = field(default_factory=dict)  # |P_c| * |P_c'|

    @property
    def components(self) -> dict[str, Component]:
        return self.inputs.components

    @property
    def settings(self) -> dict[str, Any]:
        return self.inputs.settings

    @property
    def term(self) -> str:
        return self.inputs.term

    @property
    def group_weight(self) -> dict[str, float]:
        return self.inputs.group_weight

    def pinned_components(self) -> dict[str, str]:
        """Components whose hard requests leave exactly one feasible pattern."""
        return {c: ps[0] for c, ps in self.P.items() if len(ps) == 1}

    def summary(self) -> dict[str, int]:
        return {
            "components": len(self.components),
            "patterns": len(self.patterns),
            "feasible_assignments": sum(len(v) for v in self.P.values()),
            "pinned_components": len(self.pinned_components()),
            "soft_cases": len(self.soft_cases),
            "instructor_pairs": len(self.I),
            "parent_child_pairs": len(self.H),
            "conflict_group_pairs": len(self.G),
            "C2_indices": len(self.C2_indices),
            "C3_indices": len(self.C3_indices),
            "C5_indices": len(self.C5_indices),
        }


def build_model_data(
    inputs: InputData,
    *,
    np_rates: dict[str, float] | None = None,
    strict: bool = True,
) -> ModelData:
    """Build every set and parameter the MILP needs.

    ``np_rates`` overrides the non-prime fractions, e.g. ``{"regular": 0.4, "large": 0.3}``
    (useful for sensitivity analysis). With ``strict=True`` validation errors abort.
    """
    if strict and inputs.errors():
        raise InputValidationError(f"Input workbook has data errors: {inputs.errors()}")

    components = inputs.components
    patterns = generate_patterns(
        day_start=inputs.setting_minutes("day_start"),
        day_end=inputs.setting_minutes("day_end"),
        prime_start=inputs.setting_minutes("prime_start"),
        prime_end=inputs.setting_minutes("prime_end"),
    )
    by_family: dict[str, list[str]] = defaultdict(list)
    for pid, pattern in patterns.items():
        by_family[pattern.family].append(pid)

    # ---- Feasible patterns P_c and soft-request cases --------------------------------
    P: dict[str, list[str]] = {}
    applicable: dict[str, list[Request]] = {}
    hard_removals: dict[str, dict[str, int]] = {}
    soft_cases: dict[str, SoftCase] = {}
    for cid, comp in components.items():
        reqs = [r for r in inputs.requests if applies(r, comp, inputs.group_members)]
        applicable[cid] = reqs
        candidates = list(by_family[comp.family])
        removed: dict[str, int] = {}
        for req in (r for r in reqs if r.hard):
            kept = [p for p in candidates if not violates(req, patterns[p])]
            removed[req.id] = len(candidates) - len(kept)
            candidates = kept
        P[cid] = candidates
        hard_removals[cid] = removed
        for req in (r for r in reqs if not r.hard):
            case_id = f"{req.id}@{cid}"  # no ':' -- Gurobi cannot write it in LP/MPS names
            soft_cases[case_id] = SoftCase(
                id=case_id,
                request_id=req.id,
                component_id=cid,
                weight=req.weight,
                violating=tuple(p for p in candidates if violates(req, patterns[p])),
            )

    empty = {c: hard_removals[c] for c, ps in P.items() if not ps}
    if empty:
        raise InfeasibleInputError(
            "No feasible pattern left for: "
            + "; ".join(f"{c} (family {components[c].family}, removed by {r})" for c, r in empty.items())
        )

    # ---- Component pairs --------------------------------------------------------------
    by_instructor: dict[str, list[str]] = defaultdict(list)
    for cid, comp in components.items():
        for name in comp.instructors:
            by_instructor[name].append(cid)
    I = sorted({tuple(sorted(pair)) for cids in by_instructor.values() for pair in combinations(cids, 2)})
    H = [(cid, comp.parent_id) for cid, comp in components.items()
         if comp.parent_id and comp.parent_id in components]
    G = [(gid, a, b) for gid, members in inputs.group_members.items()
         for a, b in combinations(members, 2)]

    # ---- Local overlap sets Omega_cc' (computed once per pair, reused by C2/C3/C5) ----
    omega_cache: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def omega(a: str, b: str) -> list[tuple[str, str]]:
        if (a, b) not in omega_cache:
            omega_cache[(a, b)] = [(p, q) for p in P[a] for q in P[b]
                                   if overlaps(patterns[p], patterns[q])]
        return omega_cache[(a, b)]

    C2 = [(a, b, p, q) for a, b in I for p, q in omega(a, b)]
    C3 = [(c, parent, p, q) for c, parent in H for p, q in omega(c, parent)]
    C5 = [(g, a, b, p, q) for g, a, b in G for p, q in omega(a, b)]
    pair_space = {(a, b): len(P[a]) * len(P[b]) for a, b in omega_cache}

    # ---- Non-prime quota sets C_d^R and C_d^L -----------------------------------------
    rates = {
        REGULAR: inputs.setting_float("nonprime_rate_regular"),
        LARGE: inputs.setting_float("nonprime_rate_large"),
        **(np_rates or {}),
    }
    threshold = inputs.setting_float("large_enrollment_threshold")
    np_sets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cid, comp in components.items():
        if comp.count_for_np:
            size = LARGE if comp.enrollment > threshold else REGULAR
            np_sets[(comp.department, size)].append(cid)
    np_sets = dict(sorted(np_sets.items()))
    np_required = {key: ceil_fraction(rates[key[1]], len(cids)) for key, cids in np_sets.items()}

    short = []
    for key, cids in np_sets.items():
        can_be_np = sum(any(patterns[p].nonprime for p in P[c]) for c in cids)
        if can_be_np < np_required[key]:
            short.append(f"{key}: needs {np_required[key]}, only {can_be_np} can be non-prime")
    if short:
        raise InfeasibleInputError("Non-prime quota cannot be met: " + "; ".join(short))

    return ModelData(
        inputs=inputs,
        patterns=patterns,
        P=P,
        applicable_requests=applicable,
        hard_removals=hard_removals,
        soft_cases=soft_cases,
        I=I,
        H=H,
        G=G,
        C2_indices=C2,
        C3_indices=C3,
        C5_indices=C5,
        np_sets=np_sets,
        np_required=np_required,
        np_rates=rates,
        pair_space=pair_space,
    )
