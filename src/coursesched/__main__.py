"""Command line: python -m coursesched data/raw/scheduling_input_W27.xlsx [options]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gurobipy as gp

from .checker import check_schedule
from .loader import load_workbook
from .model import SIZE_LIMITED_LICENSE, build_model, diagnose_infeasibility, solve
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
    parser.add_argument("--no-plot", action="store_true", help="skip the weekly calendar PNG")
    parser.add_argument("--quiet", action="store_true", help="hide the Gurobi log")
    args = parser.parse_args(argv)

    inputs = load_workbook(args.workbook)
    print(f"Loaded {args.workbook} (term {inputs.term}): {len(inputs.components)} components, "
          f"{len(inputs.requests)} active requests, {len(inputs.group_members)} conflict groups")
    for kind, items in {**inputs.errors(), **inputs.warnings()}.items():
        print(f"  [{'ERROR' if kind in inputs.errors() else 'warning'}] {kind}: {items}")

    try:
        data = build_model_data(inputs)
    except (InputValidationError, InfeasibleInputError) as exc:
        print(f"Cannot build the model: {exc}", file=sys.stderr)
        return 2
    print("Model data:", ", ".join(f"{k}={v}" for k, v in data.summary().items()))

    sm = build_model(data, overlap_weight=args.overlap_weight, request_weight=args.request_weight)
    try:
        solution = solve(sm, time_limit=args.time_limit, mip_gap=args.mip_gap, verbose=not args.quiet)
    except gp.GurobiError as exc:
        if exc.errno == SIZE_LIMITED_LICENSE:
            print(f"The model ({sm.model.NumVars} variables, {sm.model.NumConstrs} constraints) is too large "
                  "for Gurobi's size-limited pip license. Install the free academic license from the "
                  "Gurobi User Portal and run again.", file=sys.stderr)
            return 3
        raise
    print(f"Status {solution.status}; objective {solution.objective}; bound {solution.bound}; "
          f"gap {solution.gap}; {solution.runtime:.2f}s")
    if not solution.has_solution:
        if solution.status in {"INFEASIBLE", "INF_OR_UNBD"}:
            print("IIS constraints:", diagnose_infeasibility(sm), file=sys.stderr)
        return 1

    check = check_schedule(inputs, meetings_from_assignment(data, solution.assignment),
                           overlap_weight=sm.overlap_weight, request_weight=sm.request_weight)
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
