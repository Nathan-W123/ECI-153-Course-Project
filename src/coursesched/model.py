"""Steps 3-5 - decision variables, constraints, objective and solve (Gurobi).

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
"""

from __future__ import annotations

from dataclasses import dataclass, field

import gurobipy as gp
from gurobipy import GRB

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


@dataclass
class Solution:
    status: str
    status_code: int
    objective: float | None
    bound: float | None
    gap: float | None
    runtime: float
    assignment: dict[str, str] = field(default_factory=dict)  # component -> pattern id
    violated_soft_cases: list[str] = field(default_factory=list)
    overlapping_group_pairs: list[tuple[str, str, str]] = field(default_factory=list)

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
    model.setObjective(W_O * overlap_penalty + W_V * request_penalty, GRB.MINIMIZE)
    model.update()
    return SchedulingModel(data, model, x, y, o, W_O, W_V)


def solve(
    sm: SchedulingModel,
    *,
    time_limit: float | None = None,
    mip_gap: float | None = None,
    verbose: bool = True,
    **params: float | int,
) -> Solution:
    """Optimize and extract the schedule. Limits default to the workbook's Settings sheet."""
    inputs, model = sm.data.inputs, sm.model
    model.Params.OutputFlag = 1 if verbose else 0
    model.Params.TimeLimit = inputs.setting_float("time_limit_seconds") if time_limit is None else time_limit
    model.Params.MIPGap = inputs.setting_float("mip_gap") if mip_gap is None else mip_gap
    for name, value in params.items():
        model.setParam(name, value)

    model.optimize()

    solution = Solution(
        status=STATUS_NAMES.get(model.Status, str(model.Status)),
        status_code=model.Status,
        objective=model.ObjVal if model.SolCount else None,
        bound=model.ObjBound if model.SolCount else None,
        gap=model.MIPGap if model.SolCount else None,
        runtime=model.Runtime,
    )
    if model.SolCount:
        solution.assignment = {c: p for (c, p), var in sm.x.items() if var.X > 0.5}
        solution.violated_soft_cases = [v for v, var in sm.y.items() if var.X > 0.5]
        solution.overlapping_group_pairs = [k for k, var in sm.o.items() if var.X > 0.5]
    return solution


def diagnose_infeasibility(sm: SchedulingModel) -> list[str]:
    """Compute an Irreducible Inconsistent Subsystem and return its constraint names."""
    sm.model.computeIIS()
    return [c.ConstrName for c in sm.model.getConstrs() if c.IISConstr]
