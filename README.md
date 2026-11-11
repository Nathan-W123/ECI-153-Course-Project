# ECI 153 Course Project: Course Scheduling Optimization

A 0–1 integer linear program, solved with Gurobi, that gives every W27 course component (lectures,
labs, discussions) one weekly meeting pattern. Hard rules:

- every component gets exactly one pattern
- no instructor double-booking
- no lab or discussion overlapping its parent lecture
- each department meets its non-prime quota

It penalizes overlaps inside conflict groups and violated soft faculty preferences.

- Formulation: [`docs/formulation.md`](docs/formulation.md)
- Data audit: [`docs/data_notes.md`](docs/data_notes.md)
- Walkthrough notebook (tutorial Steps 1–6): [`notebooks/course_scheduling.ipynb`](notebooks/course_scheduling.ipynb)

## Status

| Piece | State |
|---|---|
| Steps 1–5 (load, preprocess, model, solve) | Done and tested |
| Independent feasibility checker | Done: re-verifies every hard rule and recomputes the objective |
| Excel export and weekly calendar plot | Done |
| Validation dataset | Optimal objective **320**, matching the hand calculation |
| W27 | Optimal objective **0** (no overlaps, no soft violations), cross-checked with the open-source HiGHS solver; confirm with Gurobi on a fully licensed machine |
| Step 6 analysis (sensitivity, trade-offs, tie-breaking) | Next |

## Setup

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

**Gurobi license.** The pip package ships a size-limited license (2,000 variables/constraints). That is
enough for the tests and the validation dataset, but the W27 model has about 2,160 variables and
17,900 constraints. Install the free academic license from the Gurobi User Portal. It is personal, so
never commit `gurobi.lic` (it is git-ignored).

**Input data.** The course workbooks are not committed because the repository is public. Copy them into
`data/raw/`:

```
data/raw/scheduling_input_W27.xlsx
data/raw/small_validation_dataset.xlsx
```

## Run

From the command line:

```bash
PYTHONPATH=src python -m coursesched data/raw/scheduling_input_W27.xlsx --out results
```

This writes `results/scheduling_input_W27_schedule.xlsx` (Summary, Schedule, GroupOverlaps,
SoftRequests and NonPrime sheets) and a weekly calendar PNG. Options include `--time-limit`,
`--mip-gap`, `--overlap-weight`, `--request-weight` and `--quiet`.

In the notebook, open `notebooks/course_scheduling.ipynb` and run all cells. Set `COURSESCHED_INPUT` to
use another workbook.

From Python:

```python
from coursesched import load_workbook, build_model_data, build_model, solve, check_schedule, meetings_from_assignment

inputs = load_workbook("data/raw/scheduling_input_W27.xlsx")    # Step 1
data = build_model_data(inputs)                                  # Step 2 (np_rates=... for sensitivity)
sm = build_model(data)                                           # Steps 3-4 (overlap_weight=..., request_weight=...)
solution = solve(sm)                                             # Step 5
check = check_schedule(inputs, meetings_from_assignment(data, solution.assignment))  # Step 6 audit
```

## Tests

```bash
python -m pytest
```

The tests build a synthetic copy of the validation workbook, so they run without the course files.
Tests on the real workbooks run when the files are in `data/raw/`. The full W27 solve test skips
unless a full Gurobi license is available.

## Layout

```
src/coursesched/
  patterns.py     time parsing, pattern generation, overlap and non-prime tests
  rules.py        request types: which components they apply to, which patterns violate them
  loader.py       Step 1: read, clean, validate the workbook
  preprocess.py   Step 2: P_c, soft cases, I/H/G pairs, overlap indices, non-prime quota sets
  model.py        Steps 3-5: Gurobi model, solve, infeasibility diagnosis (IIS)
  checker.py      independent audit of any schedule
  report.py       result tables and Excel export
  viz.py          weekly calendar plot
  __main__.py     command-line entry point
notebooks/        tutorial-style walkthrough
tests/            pytest suite
docs/             formulation and data notes
data/raw/         input workbooks (local only)
results/          generated outputs (git-ignored)
```

## Next steps (Step 6 analysis)

- **Break ties.** W27 reaches objective 0, so many schedules are equally optimal, and the solver's pick
  puts many classes in the 18:00–20:00 evening slots. Compare alternative optima with the solution pool
  and add a secondary objective, for example avoiding late evenings or surplus non-prime meetings.
- **Sensitivity.** Sweep the non-prime rates, the prime window and the large-enrollment threshold.
  Find where the model becomes infeasible or starts forcing overlaps.
- **Price of each hard request.** Relax one at a time and re-solve.
- **Extensions** flagged in the data: lecture–lab adjacency, non-standard patterns (WF 2-hour,
  3–5 PM starts), and splitting ECI 193's lecture hours.
