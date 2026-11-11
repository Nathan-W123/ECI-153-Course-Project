"""Solver tests. The toy model fits Gurobi's free size-limited pip license;
the full W27 test needs an academic (or other full) license and skips otherwise."""

import pytest

gp = pytest.importorskip("gurobipy")

from conftest import (  # noqa: E402
    TOY_OPTIMUM,
    TOY_REQUESTS,
    VALIDATION_PATH,
    W27_PATH,
    requires_validation,
    requires_w27,
)
from coursesched import (  # noqa: E402
    build_model,
    build_model_data,
    check_schedule,
    diagnose_infeasibility,
    load_workbook,
    meetings_from_assignment,
    solve,
)
from coursesched.model import SIZE_LIMITED_LICENSE  # noqa: E402


@pytest.fixture(scope="module")
def env():
    environment = gp.Env(params={"OutputFlag": 0})
    yield environment
    environment.dispose()


def run(path, env, **model_kwargs):
    inputs = load_workbook(path)
    data = build_model_data(inputs)
    sm = build_model(data, env=env, **model_kwargs)
    return inputs, data, sm, solve(sm, verbose=False)


def test_toy_optimum_matches_hand_calculation(toy_path, env):
    inputs, data, sm, sol = run(toy_path, env)
    assert sol.is_optimal
    assert sol.objective == pytest.approx(TOY_OPTIMUM)
    assert sol.violated_soft_cases == ["REQ-1@C144-LEC"]
    assert len(sol.overlapping_group_pairs) == 3
    check = check_schedule(inputs, meetings_from_assignment(data, sol.assignment))
    assert check.feasible, check.hard_violations
    assert check.objective == pytest.approx(sol.objective)


def test_weight_overrides(toy_path, env):
    _, _, sm, sol = run(toy_path, env, request_weight=0)
    assert sm.request_weight == 0
    assert sol.objective == pytest.approx(300)


def test_model_names_are_lp_safe(toy_path, env):
    _, _, sm, _ = run(toy_path, env)
    names = [v.VarName for v in sm.model.getVars()] + [c.ConstrName for c in sm.model.getConstrs()]
    assert not [n for n in names if ":" in n or " " in n]


def test_infeasible_model_is_diagnosed(make_workbook, env):
    # Both labs of the same instructor pinned to the same time -> C2 makes the model infeasible.
    pins = [("Y", f"PIN-{lab}", "COMPONENT", lab, "FIXED_PATTERN", "M", "17:10", "20:00", "Y", 0)
            for lab in ("C100-LAB-01", "C100-LAB-02")]
    _, _, sm, sol = run(make_workbook(requests=TOY_REQUESTS + pins), env)
    assert sol.status in {"INFEASIBLE", "INF_OR_UNBD"}
    assert not sol.has_solution
    assert any(name.startswith("C2_Instructor") for name in diagnose_infeasibility(sm))


@requires_validation
def test_real_validation_dataset(env):
    inputs, data, sm, sol = run(VALIDATION_PATH, env)
    assert sol.objective == pytest.approx(320)
    assert check_schedule(inputs, meetings_from_assignment(data, sol.assignment)).feasible


@requires_w27
def test_real_w27_dataset(env):
    try:
        inputs, data, sm, sol = run(W27_PATH, env)
    except gp.GurobiError as exc:
        if exc.errno == SIZE_LIMITED_LICENSE:
            pytest.skip("W27 needs a full Gurobi license (academic license is free)")
        raise
    assert sol.has_solution
    check = check_schedule(inputs, meetings_from_assignment(data, sol.assignment))
    assert check.feasible, check.hard_violations
    assert check.objective == pytest.approx(sol.objective)
    # Cross-checked with the open-source HiGHS solver: every overlap and soft request can be avoided.
    assert sol.objective == pytest.approx(0)
