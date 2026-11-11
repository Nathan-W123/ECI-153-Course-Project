"""Run every Step 6 analysis and write the report's tables and figures.

    PYTHONPATH=src python scripts/run_analysis.py
    PYTHONPATH=src python scripts/run_analysis.py --solver gurobi --out report

With a full Gurobi license every solve uses Gurobi; otherwise ("--solver auto")
models too large for the size-limited license are solved with HiGHS. Committed
tables and figures leave out instructor names (the input data is not public).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402

from coursesched import load_workbook  # noqa: E402
from coursesched.analysis import (  # noqa: E402
    Scenario,
    avoid_day_requests,
    changed_components,
    hard_request_relaxations,
    np_rate_scenarios,
    run_scenario,
    run_scenarios,
    start_time_profile,
    student_conflicts,
    w27_v2_scenario,
    weight_scenarios,
)
from coursesched.report import export_results, schedule_table  # noqa: E402
from coursesched.viz import plot_hbars, plot_lines, plot_start_profile, plot_weekly  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVENING_PINS = ("REQ-W27-007", "REQ-W27-008")  # approved meetings at 17:10 (Tue) and 17:10-20:00 (Mon)
PUBLIC_SCHEDULE_COLUMNS = ["component_id", "course_id", "component_type", "section", "department", "enrollment",
                           "days", "start_time", "end_time", "nonprime"]


def save(frame: pd.DataFrame, path: Path) -> pd.DataFrame:
    frame.to_csv(path, index=False, float_format="%.6g")
    print(f"  wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    return frame


def savefig(fig, path: Path) -> None:
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    print(f"  wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=ROOT / "data/raw/scheduling_input_W27.xlsx")
    parser.add_argument("--validation", type=Path, default=ROOT / "data/raw/small_validation_dataset.xlsx")
    parser.add_argument("--out", type=Path, default=ROOT / "report")
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--solver", default="auto", choices=["auto", "gurobi", "highs"])
    parser.add_argument("--time-limit", type=float, default=300)
    args = parser.parse_args(argv)

    tables, figures = args.out / "tables", args.out / "figures"
    tables.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    started = time.time()
    inputs = load_workbook(args.input)
    opts = dict(solver=args.solver, time_limit=args.time_limit)
    prime = (inputs.setting_minutes("prime_start"), inputs.setting_minutes("prime_end"))
    key: dict = {}

    def sweep(scenarios, **kwargs):
        return run_scenarios(inputs, scenarios, progress=True, **kwargs)

    # ---- A. Schedule variants at the default settings -------------------------------------------
    print("A. schedule variants")
    section_aware = Scenario("section-aware", section_aware_groups=True)
    variants = [
        ("V1 model, first optimum", Scenario("V1"), None),
        ("V1 + tie-break: off-prime minutes", Scenario("V1"), "offprime_minutes"),
        ("V1 + tie-break: evening minutes", Scenario("V1"), "evening_minutes"),
        ("+ section-aware groups", section_aware, "offprime_minutes"),
        ("+ section-aware + V2 patterns (recommended)", w27_v2_scenario(inputs, section_aware), "offprime_minutes"),
    ]
    results = {}
    for label, scenario, secondary in variants:
        results[label] = run_scenario(inputs, dataclasses.replace(scenario, name=label), secondary=secondary, **opts)
    save(pd.DataFrame([r.row for r in results.values()]), tables / "schedule_variants.csv")

    first, tied = results["V1 model, first optimum"], results["V1 + tie-break: off-prime minutes"]
    recommended = results["+ section-aware + V2 patterns (recommended)"]
    rec_schedule = schedule_table(recommended.data, recommended.solution.assignment)
    save(rec_schedule[PUBLIC_SCHEDULE_COLUMNS], tables / "recommended_schedule.csv")
    export_results(args.results, recommended.data, recommended.solution, recommended.check)  # full detail, local
    save(pd.DataFrame(student_conflicts(tied.data, tied.solution.assignment),
                      columns=["group_id", "component_a", "component_b"]), tables / "student_conflicts_v1.csv")
    save(changed_components(recommended.data, tied.solution.assignment, recommended.solution.assignment),
         tables / "changes_v1_to_recommended.csv")

    profile = start_time_profile(recommended.data, {
        "V1, first optimum": first.solution.assignment,
        "V1 + tie-break": tied.solution.assignment,
        "Recommended": recommended.solution.assignment,
    })[["V1, first optimum", "V1 + tie-break", "Recommended"]]
    save(profile.reset_index(), tables / "start_time_profile.csv")
    savefig(plot_start_profile(profile, prime_window=(prime[0] // 60, prime[1] // 60),
                               title="When classes start: weekly meeting minutes by start hour"),
            figures / "start_time_profile.png")
    savefig(plot_weekly(rec_schedule, prime_window=prime, title="Recommended W27 schedule by department"),
            figures / "weekly_recommended.png")
    savefig(plot_weekly(schedule_table(first.data, first.solution.assignment), prime_window=prime,
                        title="V1 model, solver's first optimal schedule"),
            figures / "weekly_v1_first_optimum.png")

    # ---- B. Non-prime rate sweep -------------------------------------------------------------------
    print("B. non-prime rate sweep")
    regular = [round(0.05 * i, 2) for i in range(21)]
    large = [round(0.1 * i, 1) for i in range(11)]
    np_sweep, _ = sweep(np_rate_scenarios(regular=regular, large=large), **opts)
    save(np_sweep, tables / "np_rate_sweep.csv")
    reg = np_sweep[np_sweep["varied"] == "regular"]
    infeasible = reg.loc[reg["status"].str.startswith("INFEASIBLE"), "rate"]
    first_infeasible = float(infeasible.min()) if not infeasible.empty else None
    savefig(plot_lines(reg, "rate", {"Off-prime minutes": "offprime_minutes"},
                       xlabel="Required non-prime share, regular components (default 0.35)",
                       ylabel="Weekly minutes outside prime time",
                       title="Cost of the non-prime rule (tie-broken optimum, Z = 0 throughout)",
                       infeasible_from=first_infeasible - 0.025 if first_infeasible else None,
                       infeasible_label="infeasible: too few components\ncan be non-prime"),
            figures / "np_rate_offprime.png")
    savefig(plot_lines(reg, "rate", {"8 AM starts": "early_8am_components",
                                     "Ends after 18:00": "evening_components"},
                       xlabel="Required non-prime share, regular components (default 0.35)",
                       ylabel="Components",
                       title="Where the non-prime meetings go",
                       infeasible_from=first_infeasible - 0.025 if first_infeasible else None,
                       infeasible_label="infeasible"),
            figures / "np_rate_counts.png")

    # ---- C. Policy scenarios ---------------------------------------------------------------------
    print("C. policy scenarios")
    no_friday = avoid_day_requests(inputs, "F")
    policies = [
        Scenario("Baseline: day 08:00-20:00, prime 09:00-16:00"),
        Scenario("Prime 08:00-17:00", settings={"prime_start": "08:00", "prime_end": "17:00"}),
        Scenario("Prime 09:00-17:00", settings={"prime_end": "17:00"}),
        Scenario("Prime 09:00-18:00", settings={"prime_end": "18:00"}),
        Scenario("Prime 10:00-15:00", settings={"prime_start": "10:00", "prime_end": "15:00"}),
        Scenario("Day ends 19:00", settings={"day_end": "19:00"}, drop_requests=EVENING_PINS[1:]),
        Scenario("Day ends 18:00", settings={"day_end": "18:00"}, drop_requests=EVENING_PINS[1:]),
        Scenario("Day ends 17:00", settings={"day_end": "17:00"}, drop_requests=EVENING_PINS),
        Scenario("No Friday classes", add_requests=no_friday),
        Scenario("No Friday, day ends 18:00", settings={"day_end": "18:00"}, add_requests=no_friday,
                 drop_requests=EVENING_PINS[1:]),
        Scenario("No Friday, day 08:00-16:00", settings={"day_end": "16:00"}, add_requests=no_friday,
                 drop_requests=EVENING_PINS),
    ]
    policies = [dataclasses.replace(s, tags={"dropped_requests": ", ".join(s.drop_requests)}) for s in policies]
    policy_df, policy_results = sweep(policies, **opts)
    save(policy_df, tables / "policy_scenarios.csv")
    violated = []
    for r in policy_results:
        if r.check:
            violated += [{"scenario": r.scenario.name, "request_id": rid, "component_id": cid, "weight": w}
                         for rid, cid, w in r.check.soft_violations]
    save(pd.DataFrame(violated, columns=["scenario", "request_id", "component_id", "weight"]),
         tables / "policy_soft_violations.csv")

    # ---- D. Breaking point: when do overlaps become unavoidable? ---------------------------------
    print("D. breaking point")
    # Stressed instances are hard to prove optimal with the pairwise overlap constraints, so these
    # runs stop at a 1% gap or 120 s; the bound and status columns show what was proven.
    stress_opts = dict(solver=args.solver, time_limit=min(args.time_limit, 120), mip_gap=0.01)
    breaking = [
        Scenario(f"prime 08:00-{end}, regular {rate}", np_rates={"regular": rate, "large": 0.30},
                 settings={"prime_start": "08:00", "prime_end": end}, tags={"prime_end": end, "regular_rate": rate})
        for end in ("18:00", "19:00", "19:30") for rate in (0.35, 0.50, 0.60)
    ]
    breaking_df, _ = sweep(breaking, secondary=None, **stress_opts)
    save(breaking_df, tables / "breaking_point.csv")

    # ---- E. Objective weights --------------------------------------------------------------------
    print("E. objective weights")
    stressed_soft = dataclasses.replace(policies[-1], name="No Friday, day 08:00-16:00", tags={})
    stressed_overlap = Scenario("Prime 08:00-19:00, regular 0.5", np_rates={"regular": 0.5, "large": 0.30},
                                settings={"prime_start": "08:00", "prime_end": "19:00"})
    weights = [0.2, 2, 20, 200, 2000]
    weight_df, _ = run_scenarios(
        inputs, weight_scenarios(weights, stressed_soft) + weight_scenarios(weights, stressed_overlap),
        secondary=None, **stress_opts)
    save(weight_df, tables / "weight_sweep.csv")

    # ---- F. What each approved hard request costs -------------------------------------------------
    print("F. hard-request relaxations")
    base_row = tied.row
    relax_df, _ = sweep(hard_request_relaxations(inputs, Scenario("V1")), **opts)
    for col in ("offprime_minutes", "evening_components", "early_8am_components", "student_conflicts"):
        relax_df[f"delta_{col}"] = relax_df[col] - base_row[col]
    save(relax_df, tables / "hard_request_relaxation.csv")
    stressed_base = run_scenario(inputs, stressed_soft, secondary=None, **stress_opts).row
    relax_stressed, _ = sweep(hard_request_relaxations(inputs, stressed_soft),
                                      secondary=None, **stress_opts)
    relax_stressed["delta_objective"] = relax_stressed["objective"] - stressed_base["objective"]
    save(relax_stressed, tables / "hard_request_relaxation_stressed.csv")
    ordered = relax_df.sort_values("delta_offprime_minutes")
    savefig(plot_hbars([f"{d} ({t})" for d, t in zip(ordered["dropped"], ordered["target"], strict=True)],
                       ordered["delta_offprime_minutes"],
                       xlabel="Change in weekly off-prime minutes if the request were dropped",
                       title="What each approved hard request costs (Z stays 0)"),
            figures / "hard_request_cost.png")

    # ---- G. Validation dataset -------------------------------------------------------------------
    validation_rows = []
    if args.validation.exists():
        print("G. validation dataset")
        vinputs = load_workbook(args.validation)
        for label, secondary in (("first optimum", None), ("tie-break: off-prime minutes", "offprime_minutes")):
            r = run_scenario(vinputs, Scenario(f"validation, {label}"), secondary=secondary, **opts)
            validation_rows.append(r.row)
        save(pd.DataFrame(validation_rows), tables / "validation_dataset.csv")

    # ---- Key numbers for the report ---------------------------------------------------------------
    key.update({
        "variants": {label: r.row for label, r in results.items()},
        "np_first_infeasible_regular_rate": first_infeasible,
        "changed_components_v1_to_recommended": int(
            len(changed_components(recommended.data, tied.solution.assignment, recommended.solution.assignment))),
        "stressed_soft_objective": stressed_base.get("objective"),
        "solvers_used": sorted({r.row.get("solver") for r in results.values() if r.row.get("solver")}),
        "runtime_seconds": round(time.time() - started, 1),
        "python": platform.python_version(),
    })
    (tables / "key_numbers.json").write_text(json.dumps(key, indent=2, default=str))
    print(f"Done in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
