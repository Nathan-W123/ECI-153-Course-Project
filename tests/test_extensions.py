"""Model extensions: HiGHS fallback, lexicographic tie-break, custom patterns, section-aware groups."""

import pytest

gp = pytest.importorskip("gurobipy")

from conftest import TOY_OPTIMUM, TOY_REQUESTS  # noqa: E402
from coursesched import build_model, build_model_data, check_schedule, load_workbook, solve  # noqa: E402
from coursesched.model import evening_minutes, offprime_minutes, tie_break  # noqa: E402
from coursesched.patterns import Block, Pattern  # noqa: E402
from coursesched.preprocess import InfeasibleInputError  # noqa: E402
from coursesched.report import meetings_from_assignment  # noqa: E402

NONSTANDARD_PIN = ("Y", "PIN", "COMPONENT", "C144-LAB-01", "FIXED_PATTERN", "R", "15:00", "16:50", "Y", 0)


@pytest.fixture(scope="module")
def env():
    environment = gp.Env(params={"OutputFlag": 0})
    yield environment
    environment.dispose()


def toy_model(path, env, **data_kwargs):
    inputs = load_workbook(path)
    data = build_model_data(inputs, **data_kwargs)
    return inputs, data, build_model(data, env=env)


def test_highs_matches_gurobi(toy_path, env):
    pytest.importorskip("highspy")
    _, _, sm = toy_model(toy_path, env)
    by_gurobi = solve(sm, verbose=False, solver="gurobi")
    by_highs = solve(sm, verbose=False, solver="highs")
    assert by_highs.solver == "highs" and by_highs.is_optimal
    assert by_highs.objective == pytest.approx(by_gurobi.objective) == pytest.approx(TOY_OPTIMUM)
    assert sorted(by_highs.overlapping_group_pairs) == sorted(by_gurobi.overlapping_group_pairs)


def test_auto_uses_gurobi_when_the_license_allows(toy_path, env):
    _, _, sm = toy_model(toy_path, env)
    assert solve(sm, verbose=False, solver="auto").solver == "gurobi"
    with pytest.raises(ValueError, match="Unknown solver"):
        solve(sm, verbose=False, solver="cplex")


def test_secondary_costs():
    evening = Pattern("p", "SINGLE_170", "M", 17 * 60 + 10, 20 * 60, True)
    early_mwf = Pattern("q", "LEC3", "MWF", 8 * 60, 8 * 60 + 50, True)
    straddle = Pattern("r", "SINGLE_170", "T", 14 * 60 + 10, 17 * 60, True)
    prime = (9 * 60, 16 * 60)
    assert offprime_minutes(evening, *prime) == 170
    assert offprime_minutes(early_mwf, *prime) == 150  # 50 minutes x 3 days
    assert offprime_minutes(straddle, *prime) == 60
    assert evening_minutes(evening) == 170 and evening_minutes(early_mwf) == 0 and evening_minutes(straddle) == 0


@pytest.mark.parametrize("solver", ["gurobi", "highs"])
def test_tie_break_keeps_optimum_and_restores_model(toy_path, env, solver):
    if solver == "highs":
        pytest.importorskip("highspy")
    inputs, data, sm = toy_model(toy_path, env)
    constraints = sm.model.NumConstrs
    first = solve(sm, verbose=False, solver=solver)
    second = tie_break(sm, first, verbose=False, solver=solver)
    assert second.objective == pytest.approx(TOY_OPTIMUM)
    # Three non-prime meetings are required; the cheapest are 50-minute ones at 08:00 or 16:10.
    assert second.secondary == "offprime_minutes"
    assert second.secondary_objective == pytest.approx(170)
    check = check_schedule(inputs, meetings_from_assignment(data, second.assignment))
    assert check.feasible and check.objective == pytest.approx(TOY_OPTIMUM)
    assert sm.model.NumConstrs == constraints
    assert solve(sm, verbose=False, solver=solver).objective == pytest.approx(TOY_OPTIMUM)


def test_custom_patterns_honor_nonstandard_fixed_requests(make_workbook, env):
    path = make_workbook(requests=TOY_REQUESTS + [NONSTANDARD_PIN])
    with pytest.raises(InfeasibleInputError, match="C144-LAB-01"):
        build_model_data(load_workbook(path))
    inputs, data, sm = toy_model(path, env, custom_patterns=True)
    assert data.custom_patterns == {"C144-LAB-01": ["CUSTOM_SINGLE_110_R_1500_1650"]}
    assert data.P["C144-LAB-01"] == ["CUSTOM_SINGLE_110_R_1500_1650"]
    assert data.patterns["CUSTOM_SINGLE_110_R_1500_1650"].nonprime  # ends 16:50
    solution = solve(sm, verbose=False)
    assert solution.assignment["C144-LAB-01"] == "CUSTOM_SINGLE_110_R_1500_1650"
    check = check_schedule(inputs, meetings_from_assignment(data, solution.assignment))
    assert check.feasible, check.hard_violations  # an approved fixed meeting is a valid pattern


def test_custom_patterns_outside_the_day_are_not_added(make_workbook):
    late = ("Y", "PIN", "COMPONENT", "C144-LAB-01", "FIXED_PATTERN", "R", "19:00", "20:50", "Y", 0)
    with pytest.raises(InfeasibleInputError):
        build_model_data(load_workbook(make_workbook(requests=TOY_REQUESTS + [late])), custom_patterns=True)


def test_section_aware_groups(toy_path, env):
    inputs = load_workbook(toy_path)
    assert len(inputs.group_pairs()) == 3
    pairs = inputs.group_pairs(section_aware=True)
    # Families: C100 = lecture + 2 labs, C144 = lecture + lab, C146 = lecture + discussion.
    assert len(pairs) == 3 * 2 + 3 * 2 + 2 * 2
    assert ("LECTURES", "C100-LAB-01", "C144-LEC") in pairs
    assert not any({a, b} <= {"C100-LEC", "C100-LAB-01", "C100-LAB-02"} for _, a, b in pairs)

    data = build_model_data(inputs, section_aware_groups=True)
    assert data.section_aware_groups and len(data.G) == 16
    sm = build_model(data, env=env)
    solution = solve(sm, verbose=False)
    # Lectures still collide (forced); sections can all be kept clear, so Z is unchanged.
    assert solution.objective == pytest.approx(TOY_OPTIMUM)
    check = check_schedule(inputs, meetings_from_assignment(data, solution.assignment), section_aware_groups=True)
    assert check.objective == pytest.approx(solution.objective)


def test_checker_counts_section_overlaps(toy_path):
    inputs = load_workbook(toy_path)
    meetings = {
        "C100-LEC": Block("MWF", 600, 650), "C144-LEC": Block("MWF", 600, 650), "C146-LEC": Block("MWF", 600, 650),
        "C100-LAB-01": Block("M", 970, 1140), "C100-LAB-02": Block("T", 970, 1140),
        "C144-LAB-01": Block("M", 970, 1080),  # overlaps C100-LAB-01 on Monday
        "C146-DIS-01": Block("F", 540, 590),
    }
    plain = check_schedule(inputs, meetings)
    aware = check_schedule(inputs, meetings, section_aware_groups=True)
    assert plain.feasible and aware.feasible
    assert aware.objective - plain.objective == pytest.approx(100)
    assert ("LECTURES", "C100-LAB-01", "C144-LAB-01", 1.0) in aware.group_overlaps


@pytest.mark.parametrize("flags", [[], ["--recommended"], ["--tie-break", "evening_minutes", "--section-aware"]])
def test_command_line(toy_path, tmp_path, flags, capsys):
    from coursesched.__main__ import main

    assert main([str(toy_path), "--out", str(tmp_path), "--quiet", *flags]) == 0
    out = capsys.readouterr().out
    assert "Recomputed objective : 320" in out
    assert (tmp_path / "toy_schedule.xlsx").exists() and (tmp_path / "toy_weekly.png").exists()
