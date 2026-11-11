"""Command line: python -m coursesched data/raw/scheduling_input_W27.xlsx [options]

--recommended reproduces the report's recommended schedule: section-aware conflict groups,
custom patterns for the two accepted W27 requests, and the off-prime tie-break.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gurobipy as gp

from .checker import check_schedule
from .loader import load_workbook
from .model import SIZE_LIMITED_LICENSE, build_model, diagnose_infeasibility, solve, tie_break
from .preprocess import InfeasibleInputError, InputValidationError, build_model_data
from .report import export_results, meetings_from_assignment, schedule_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coursesched", description=__doc__)
    parser.add_argument("workbook", type=Path, help="input Excel workbook")
    parser.add_argument("--out", type=Path, default=Path("results"), help="output directory")
    parser.add_argument("--time-limit", type=float, help="seconds (default: Settings sheet)")
    parser.add_argument("--mip-gap", type=float, help="relative gap (default: Settings sheet)")
    parser.add_argument("--overlap-weight", type=float, help="W_O (default: Settings sheet)")
    parser.add_argument("--request-weight", type=float, help="W_V (default: Settings sheet)")
    parser.add_argument("--solver", choices=["gurobi", "highs", "auto"], default="gurobi",
                        help="auto = Gurobi, falling back to HiGHS when the license is size-limited")
    parser.add_argument("--tie-break", choices=["offprime_minutes", "evening_minutes"],
                        help="second stage: keep the optimal Z, minimize this secondary cost")
    parser.add_argument("--section-aware", action="store_true", help="extension: section-aware conflict groups")
    parser.add_argument("--custom-patterns", action="store_true",
                        help="extension: approved non-standard FIXED_PATTERN meetings become candidates")
    parser.add_argument("--recommended", action="store_true",
                        help="section-aware + custom patterns (W27 accepted requests) + off-prime tie-break")
    parser.add_argument("--no-plot", action="store_true", help="skip the weekly calendar PNG")
    parser.add_argument("--quiet", action="store_true", help="hide the Gurobi log")
    args = parser.parse_args(argv)

    inputs = load_workbook(args.workbook)
    print(f"Loaded {args.workbook} (term {inputs.term}): {len(inputs.components)} components, "
          f"{len(inputs.requests)} active requests, {len(inputs.group_members)} conflict groups")
    for kind, items in {**inputs.errors(), **inputs.warnings()}.items():
        print(f"  [{'ERROR' if kind in inputs.errors() else 'warning'}] {kind}: {items}")

    if args.recommended:
        from .analysis import apply_scenario, w27_v2_scenario

        inputs = apply_scenario(inputs, w27_v2_scenario(inputs))
        args.section_aware, args.custom_patterns = True, True
        args.tie_break = args.tie_break or "offprime_minutes"
    try:
        data = build_model_data(inputs, custom_patterns=args.custom_patterns,
                                section_aware_groups=args.section_aware)
    except (InputValidationError, InfeasibleInputError) as exc:
        print(f"Cannot build the model: {exc}", file=sys.stderr)
        return 2
    print("Model data:", ", ".join(f"{k}={v}" for k, v in data.summary().items()))

    sm = build_model(data, overlap_weight=args.overlap_weight, request_weight=args.request_weight)
    try:
        kwargs = dict(time_limit=args.time_limit, mip_gap=args.mip_gap, verbose=not args.quiet, solver=args.solver)
        solution = solve(sm, **kwargs)
        if args.tie_break and solution.has_solution:
            solution = tie_break(sm, solution, secondary=args.tie_break, **kwargs)
            print(f"Tie-break ({args.tie_break}): {solution.status}, secondary cost {solution.secondary_objective}")
    except gp.GurobiError as exc:
        if exc.errno == SIZE_LIMITED_LICENSE:
            print(f"The model ({sm.model.NumVars} variables, {sm.model.NumConstrs} constraints) is too large "
                  "for Gurobi's size-limited pip license. Install the free academic license from the "
                  "Gurobi User Portal, or run with --solver auto.", file=sys.stderr)
            return 3
        raise
    print(f"Status {solution.status}; objective {solution.objective}; bound {solution.bound}; "
          f"gap {solution.gap}; {solution.runtime:.2f}s")
    if not solution.has_solution:
        if solution.status in {"INFEASIBLE", "INF_OR_UNBD"}:
            print("IIS constraints:", diagnose_infeasibility(sm), file=sys.stderr)
        return 1

    check = check_schedule(inputs, meetings_from_assignment(data, solution.assignment),
                           overlap_weight=sm.overlap_weight, request_weight=sm.request_weight,
                           section_aware_groups=args.section_aware)
    print("Independent check:\n" + check.summary())
    if abs(check.objective - solution.objective) > 1e-6:
        print(f"WARNING: checker objective {check.objective} != solver objective {solution.objective}")

    path = export_results(args.out, data, solution, check)
    print(f"Wrote {path}")
    if not args.no_plot:
        from .viz import plot_weekly

        fig = plot_weekly(schedule_table(data, solution.assignment),
                          prime_window=(inputs.setting_minutes("prime_start"),
                                        inputs.setting_minutes("prime_end")))
        png = args.out / f"{inputs.source.stem}_weekly.png"
        fig.savefig(png, dpi=150)
        print(f"Wrote {png}")
    return 0 if check.feasible else 1


if __name__ == "__main__":
    sys.exit(main())
