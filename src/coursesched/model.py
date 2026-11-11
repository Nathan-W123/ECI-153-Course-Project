"""Steps 3-5 - decision variables, constraints, objective and solve.

    x[c,p]    1 if component c is assigned pattern p                (p in P_c)
    y[v]      1 if soft-request case v is violated                  (v in V)
    o[j,c,c'] 1 if c and c' overlap inside conflict group j         ((j,c,c') in G)

    C1  sum_p x[c,p] = 1                                  every component gets one pattern
    C2  x[c,p] + x[c',p'] <= 1                            same instructor, overlapping (p,p')
    C3  x[c,p] + x[c',p'] <= 1                            child/parent, overlapping (p,p')
    C4  y[v] >= x[c(v),p]                                 p in B_v
    C5  x[c,p] + x[c',p'] - o[j,c,c'] <= 1                conflict group j, overlapping (p,p')
    C6  sum_{c in C_d^R} sum_{p in P_c^NP} x[c,p] >= ceil(rate_R |C_d^R|)
    C7  sum_{c in C_d^L} sum_{p in P_c^NP} x[c,p] >= ceil(rate_L |C_d^L|)

    min  W_O * sum_j w_j sum o[j,c,c']  +  W_V * sum_v w_v y[v]

The model is always built with gurobipy. It is solved with Gurobi, or -- when no
full Gurobi license is available -- exported to MPS and solved with the open-source
HiGHS solver (``solver="highs"`` or ``solver="auto"``).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

from .patterns import Pattern
from .preprocess import LARGE, ModelData

SIZE_LIMITED_LICENSE = 10010  # GurobiError.errno: model too large for the size-limited license

STATUS_NAMES = {
    GRB.OPTIMAL: "OPTIMAL",
    GRB.INFEASIBLE: "INFEASIBLE",
    GRB.INF_OR_UNBD: "INF_OR_UNBD",
    GRB.UNBOUNDED: "UNBOUNDED",
    GRB.TIME_LIMIT: "TIME_LIMIT",
    GRB.INTERRUPTED: "INTERRUPTED",
    GRB.SOLUTION_LIMIT: "SOLUTION_LIMIT",
    GRB.NODE_LIMIT: "NODE_LIMIT",
}


@dataclass
class SchedulingModel:
    data: ModelData
    model: gp.Model
    x: gp.tupledict
    y: gp.tupledict
    o: gp.tupledict
    overlap_weight: float
    request_weight: float
    primary: gp.LinExpr  # W_O * overlaps + W_V * soft violations


@dataclass
class Solution:
    status: str
    status_code: int
    objective: float | None  # value of the primary objective Z
    bound: float | None
    gap: float | None
    runtime: float
    assignment: dict[str, str] = field(default_factory=dict)  # component -> pattern id
    violated_soft_cases: list[str] = field(default_factory=list)
    overlapping_group_pairs: list[tuple[str, str, str]] = field(default_factory=list)
    solver: str = "gurobi"
    secondary: str | None = None  # name of the tie-break objective, if one was used
    secondary_objective: float | None = None

    @property
    def has_solution(self) -> bool:
        return bool(self.assignment)

    @property
    def is_optimal(self) -> bool:
        return self.status_code == GRB.OPTIMAL


def build_model(
    data: ModelData,
    *,
    overlap_weight: float | None = None,
    request_weight: float | None = None,
    env: gp.Env | None = None,
) -> SchedulingModel:
    """Build the 0-1 ILP. Weights default to the workbook's Settings sheet."""
    W_O = data.inputs.setting_float("overlap_weight") if overlap_weight is None else float(overlap_weight)
    W_V = data.inputs.setting_float("request_weight") if request_weight is None else float(request_weight)
    P, patterns = data.P, data.patterns

    model = gp.Model(f"Course_Scheduling_{data.term}", env=env)

    # ---- Step 3: decision variables ----------------------------------------------------
    CP = [(c, p) for c in data.components for p in P[c]]
    # A soft case nothing can violate (empty B_v) needs no variable.
    V = [v for v, case in data.soft_cases.items() if case.violating]
    # A group pair with no overlapping pattern pair can never overlap; skip its variable.
    OG = sorted({(g, a, b) for g, a, b, _, _ in data.C5_indices})

    x = model.addVars(CP, vtype=GRB.BINARY, name="x")
    y = model.addVars(V, vtype=GRB.BINARY, name="y")
    o = model.addVars(OG, vtype=GRB.BINARY, name="o")

    # ---- Step 4: constraints -----------------------------------------------------------
    model.addConstrs((x.sum(c, "*") == 1 for c in data.components), name="C1_ExactlyOne")
    model.addConstrs((x[a, p] + x[b, q] <= 1 for a, b, p, q in data.C2_indices), name="C2_Instructor")
    model.addConstrs((x[c, p] + x[d, q] <= 1 for c, d, p, q in data.C3_indices), name="C3_ParentChild")
    model.addConstrs(
        (y[v] >= x[data.soft_cases[v].component_id, p] for v in V for p in data.soft_cases[v].violating),
        name="C4_SoftRequest",
    )
    model.addConstrs((x[a, p] + x[b, q] - o[g, a, b] <= 1 for g, a, b, p, q in data.C5_indices),
                     name="C5_GroupOverlap")
    for (dept, size), members in data.np_sets.items():
        nonprime_assignments = gp.quicksum(x[c, p] for c in members for p in P[c] if patterns[p].nonprime)
        tag = "C7_NonPrimeLarge" if size == LARGE else "C6_NonPrimeRegular"
        model.addConstr(nonprime_assignments >= data.np_required[(dept, size)], name=f"{tag}[{dept}]")

    # ---- Objective -----------------------------------------------------------------------
    overlap_penalty = gp.quicksum(data.group_weight[g] * o[g, a, b] for g, a, b in OG)
    request_penalty = gp.quicksum(data.soft_cases[v].weight * y[v] for v in V)
    primary = W_O * overlap_penalty + W_V * request_penalty
    model.setObjective(primary, GRB.MINIMIZE)
    model.update()
    return SchedulingModel(data, model, x, y, o, W_O, W_V, primary)


# ---- Step 5: solve ----------------------------------------------------------------------

def solve(
    sm: SchedulingModel,
    *,
    time_limit: float | None = None,
    mip_gap: float | None = None,
    verbose: bool = True,
    solver: str = "gurobi",
    **params: float | int,
) -> Solution:
    """Optimize and extract the schedule. Limits default to the workbook's Settings sheet.

    ``solver``: "gurobi", "highs", or "auto" (Gurobi, falling back to HiGHS when the
    installed Gurobi license is too small for the model). Extra ``params`` are Gurobi parameters.
    """
    inputs = sm.data.inputs
    time_limit = inputs.setting_float("time_limit_seconds") if time_limit is None else float(time_limit)
    mip_gap = inputs.setting_float("mip_gap") if mip_gap is None else float(mip_gap)
    if solver == "highs":
        return _solve_highs(sm, time_limit, mip_gap, verbose)
    if solver not in {"gurobi", "auto"}:
        raise ValueError(f"Unknown solver {solver!r}; use 'gurobi', 'highs' or 'auto'")
    try:
        return _solve_gurobi(sm, time_limit, mip_gap, verbose, params)
    except gp.GurobiError as exc:
        if solver == "auto" and exc.errno == SIZE_LIMITED_LICENSE:
            if verbose:
                print("Gurobi license is size-limited for this model; solving with HiGHS instead.")
            return _solve_highs(sm, time_limit, mip_gap, verbose)
        raise


def _solution_from_values(sm: SchedulingModel, value: Callable[[gp.Var], float], **fields) -> Solution:
    solution = Solution(**fields)
    solution.assignment = {c: p for (c, p), var in sm.x.items() if value(var) > 0.5}
    solution.violated_soft_cases = [v for v, var in sm.y.items() if value(var) > 0.5]
    solution.overlapping_group_pairs = [k for k, var in sm.o.items() if value(var) > 0.5]
    return solution


def _solve_gurobi(sm: SchedulingModel, time_limit: float, mip_gap: float, verbose: bool, params) -> Solution:
    model = sm.model
    model.Params.OutputFlag = 1 if verbose else 0
    model.Params.TimeLimit = time_limit
    model.Params.MIPGap = mip_gap
    for name, value in params.items():
        model.setParam(name, value)
    model.optimize()
    fields = dict(
        status=STATUS_NAMES.get(model.Status, str(model.Status)),
        status_code=model.Status,
        objective=model.ObjVal if model.SolCount else None,
        bound=model.ObjBound if model.SolCount else None,
        gap=model.MIPGap if model.SolCount else None,
        runtime=model.Runtime,
        solver="gurobi",
    )
    if not model.SolCount:
        return Solution(**fields)
    return _solution_from_values(sm, lambda var: var.X, **fields)


def _solve_highs(sm: SchedulingModel, time_limit: float, mip_gap: float, verbose: bool) -> Solution:
    import highspy

    statuses = {
        highspy.HighsModelStatus.kOptimal: GRB.OPTIMAL,
        highspy.HighsModelStatus.kInfeasible: GRB.INFEASIBLE,
        highspy.HighsModelStatus.kUnboundedOrInfeasible: GRB.INF_OR_UNBD,
        highspy.HighsModelStatus.kUnbounded: GRB.UNBOUNDED,
        highspy.HighsModelStatus.kTimeLimit: GRB.TIME_LIMIT,
        highspy.HighsModelStatus.kSolutionLimit: GRB.SOLUTION_LIMIT,
        highspy.HighsModelStatus.kInterrupt: GRB.INTERRUPTED,
    }
    variables = sm.model.getVars()
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "model.mps")
        sm.model.write(path)
        h = highspy.Highs()
        if not verbose:
            h.silent()
        h.setOptionValue("time_limit", float(time_limit))
        h.setOptionValue("mip_rel_gap", float(mip_gap))
        h.readModel(path)
        h.run()

    names = list(h.getLp().col_names_)
    if names != [v.VarName for v in variables]:
        raise RuntimeError("HiGHS column order does not match the Gurobi model")
    status = h.getModelStatus()
    info = h.getInfo()
    code = statuses.get(status, -1)
    fields = dict(
        status=STATUS_NAMES.get(code, f"HIGHS_{h.modelStatusToString(status).upper()}"),
        status_code=code,
        objective=None,
        bound=None,
        gap=None,
        runtime=h.getRunTime(),
        solver="highs",
    )
    if info.primal_solution_status != 2:  # 2 = feasible solution available
        return Solution(**fields)
    fields.update(objective=info.objective_function_value, bound=info.mip_dual_bound, gap=info.mip_gap)
    values = dict(zip(names, h.getSolution().col_value, strict=True))
    return _solution_from_values(sm, lambda var: values[var.VarName], **fields)


def diagnose_infeasibility(sm: SchedulingModel) -> list[str]:
    """Compute an Irreducible Inconsistent Subsystem (Gurobi) and return its constraint names."""
    sm.model.computeIIS()
    return [c.ConstrName for c in sm.model.getConstrs() if c.IISConstr]


# ---- Tie-breaking among optimal schedules ---------------------------------------------------

def offprime_minutes(pattern: Pattern, prime_start: int, prime_end: int) -> int:
    """Weekly meeting minutes that fall outside the prime window."""
    per_day = min(pattern.duration, max(0, prime_start - pattern.start) + max(0, pattern.end - prime_end))
    return per_day * len(pattern.days)


def evening_minutes(pattern: Pattern, cutoff: int = 17 * 60) -> int:
    """Weekly meeting minutes after ``cutoff`` (default 17:00)."""
    return max(0, pattern.end - max(pattern.start, cutoff)) * len(pattern.days)


def secondary_costs(data: ModelData, kind: str) -> dict[str, float]:
    """Per-pattern cost for a tie-break objective."""
    prime = data.inputs.setting_minutes("prime_start"), data.inputs.setting_minutes("prime_end")
    if kind == "offprime_minutes":
        return {pid: offprime_minutes(p, *prime) for pid, p in data.patterns.items()}
    if kind == "evening_minutes":
        return {pid: evening_minutes(p) for pid, p in data.patterns.items()}
    raise ValueError(f"Unknown secondary objective {kind!r}")


def tie_break(
    sm: SchedulingModel,
    first: Solution,
    *,
    secondary: str = "offprime_minutes",
    **solve_kwargs,
) -> Solution:
    """Lexicographic second stage: keep Z at its optimum, then minimize a secondary cost.

    Many schedules can share the optimal Z (for W27, Z* = 0). The second stage adds
    ``Z <= Z*`` and picks, among those schedules, one with the least secondary cost --
    by default the total weekly minutes taught outside the prime window, which steers
    the required non-prime meetings to the shoulders of the day (08:00, 16:10)
    instead of late evening. The model is restored afterwards.
    """
    if not first.has_solution:
        return first
    model = sm.model
    cost = secondary_costs(sm.data, secondary)
    lex = model.addConstr(sm.primary <= first.objective + 1e-6, name="Lex_PrimaryAtOptimum")
    model.setObjective(gp.quicksum(cost[p] * var for (_, p), var in sm.x.items()), GRB.MINIMIZE)
    try:
        second = solve(sm, **solve_kwargs)
    finally:
        model.remove(lex)
        model.setObjective(sm.primary, GRB.MINIMIZE)
        model.update()
    if second.has_solution:
        second.secondary, second.secondary_objective = secondary, second.objective
        # objective/bound/gap describe Z (stage 1); the stage-2 value is secondary_objective.
        second.bound, second.gap = first.bound, first.gap
        second.runtime += first.runtime
        weights = sm.data.group_weight
        cases = sm.data.soft_cases
        second.objective = (sm.overlap_weight * sum(weights[g] for g, _, _ in second.overlapping_group_pairs)
                            + sm.request_weight * sum(cases[v].weight for v in second.violated_soft_cases))
    return second
