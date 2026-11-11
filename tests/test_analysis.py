import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from conftest import TOY_OPTIMUM  # noqa: E402
from coursesched import build_model_data, load_workbook  # noqa: E402
from coursesched.analysis import (  # noqa: E402
    Scenario,
    apply_scenario,
    avoid_day_requests,
    changed_components,
    hard_request_relaxations,
    np_rate_scenarios,
    schedule_metrics,
    start_time_profile,
    student_conflicts,
    w27_v2_scenario,
    weight_scenarios,
)
from coursesched.report import schedule_table  # noqa: E402
from coursesched.viz import plot_hbars, plot_lines, plot_start_profile, plot_weekly  # noqa: E402

# A hand-built optimal toy schedule, as pattern ids.
OPTIMAL = {
    "C100-LEC": "LEC3_MWF_1000_1050",
    "C100-LAB-01": "SINGLE_170_M_1610_1900",
    "C100-LAB-02": "SINGLE_170_T_1610_1900",
    "C144-LEC": "LEC3_MWF_1000_1050",
    "C144-LAB-01": "SINGLE_110_R_1610_1800",
    "C146-LEC": "LEC3_MWF_1000_1050",
    "C146-DIS-01": "SINGLE_50_F_0900_0950",
}


@pytest.fixture
def toy(toy_path):
    inputs = load_workbook(toy_path)
    return inputs, build_model_data(inputs)


def test_schedule_metrics(toy):
    _, data = toy
    metrics = schedule_metrics(data, OPTIMAL)
    assert metrics["nonprime_components"] == metrics["nonprime_required"] == 3
    assert metrics["offprime_minutes"] == 170 + 170 + 110  # the three labs after 16:00
    assert metrics["evening_components"] == 2 and metrics["early_8am_components"] == 0
    assert metrics["friday_components"] == 4  # three MWF lectures + the Friday discussion
    assert metrics["student_conflicts"] == 3  # the forced lecture overlaps make every pair untakeable


def test_student_conflicts_respect_section_choice(toy):
    _, data = toy
    # Move lectures apart: no pair conflicts any more.
    spread = {**OPTIMAL, "C144-LEC": "LEC3_TR_1030_1150", "C146-LEC": "LEC3_MWF_1100_1150"}
    assert student_conflicts(data, spread) == []
    # C146's only discussion clashes with C144's lecture -> that pair cannot be taken together.
    clash = {**spread, "C146-DIS-01": "SINGLE_50_T_1100_1150"}
    assert student_conflicts(data, clash) == [("LECTURES", "C144-LEC", "C146-LEC")]
    # C100 has two labs: one clashing lab is fine as long as the other section is free.
    one_lab = {**spread, "C100-LAB-01": "SINGLE_170_T_0900_1150"}  # overlaps C144's TR lecture
    assert student_conflicts(data, one_lab) == []


def test_scenario_application(toy):
    inputs, _ = toy
    changed = apply_scenario(inputs, Scenario("x", settings={"day_end": "18:00"}, drop_requests=("REQ-1",)))
    assert changed.settings["day_end"] == "18:00" and inputs.settings["day_end"] == "20:00"
    assert "REQ-1" not in {r.id for r in changed.requests}
    with pytest.raises(ValueError, match="unknown requests"):
        apply_scenario(inputs, Scenario("x", drop_requests=("NOPE",)))


def test_scenario_builders(toy):
    inputs, _ = toy
    rates = np_rate_scenarios(regular=[0.4, 0.5], large=[1.0])
    assert [s.np_rates for s in rates] == [{"regular": 0.4, "large": 0.3}, {"regular": 0.5, "large": 0.3},
                                           {"regular": 0.35, "large": 1.0}]
    assert [s.request_weight for s in weight_scenarios([1, 5], Scenario("b"))] == [1, 5]
    no_friday = avoid_day_requests(inputs, "F")
    assert len(no_friday) == 7 and all(r.kind == "AVOID_DAYS" and r.hard for r in no_friday)
    # The toy has no hard Requests-sheet rows (FixedSchedule rows are excluded on purpose).
    assert hard_request_relaxations(inputs, Scenario("b")) == []
    v2 = w27_v2_scenario(inputs)  # W27-specific components are absent from the toy: nothing added
    assert v2.custom_patterns and v2.add_requests == () and v2.drop_requests == ()


def test_run_scenario_end_to_end(toy_path):
    pytest.importorskip("gurobipy")
    from coursesched.analysis import run_scenario, run_scenarios

    inputs = load_workbook(toy_path)
    result = run_scenario(inputs, Scenario("toy"), solver="gurobi", time_limit=30)
    assert result.row["objective"] == pytest.approx(TOY_OPTIMUM)
    assert result.row["checker_feasible"] and result.row["offprime_minutes"] == 170
    table, _ = run_scenarios(inputs, [Scenario("ok"), Scenario("too strict", np_rates={"regular": 1.0})],
                             solver="gurobi", time_limit=30)
    assert list(table["status"]) == ["OPTIMAL", "INFEASIBLE (pre-check)"]


def test_comparison_tables_and_figures(toy):
    inputs, data = toy
    moved = {**OPTIMAL, "C146-DIS-01": "SINGLE_50_F_0800_0850"}
    profile = start_time_profile(data, {"a": OPTIMAL, "b": moved})
    assert profile.loc[9, "a"] == 50 and profile.loc[8, "b"] == 50
    changes = changed_components(data, OPTIMAL, moved)
    assert list(changes["component_id"]) == ["C146-DIS-01"]

    figures = [
        plot_weekly(schedule_table(data, OPTIMAL)),
        plot_start_profile(profile),
        plot_lines(pd.DataFrame({"x": [0.1, 0.2, 0.3], "y": [1, 2, None], "z": [3, 2, 1]}), "x",
                   {"Y": "y", "Z": "z"}, xlabel="x", ylabel="y", infeasible_from=0.25),
        plot_hbars(["a", "b"], [1, -2], xlabel="delta"),
    ]
    assert all(len(fig.axes) >= 1 for fig in figures)
