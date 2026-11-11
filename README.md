# ECI 153 Course Project: Course Scheduling Optimization

A 0–1 integer linear program, solved with Gurobi, that gives every W27 course component (lectures,
labs, discussions) one weekly meeting pattern. Hard rules:

- every component gets exactly one pattern
- no instructor double-booking
- no lab or discussion overlapping its parent lecture
- each department meets its non-prime quota

It penalizes overlaps inside conflict groups and violated soft faculty preferences.

| Deliverable | Where |
|---|---|
| **Project report**: results, analysis, recommendations | [`report/report.md`](report/report.md) |
| Mathematical formulation and extensions | [`docs/formulation.md`](docs/formulation.md) |
| Data audit | [`docs/data_notes.md`](docs/data_notes.md) |
| Walkthrough notebook (tutorial Steps 1–6) | [`notebooks/course_scheduling.ipynb`](notebooks/course_scheduling.ipynb) |
| Code: `coursesched` package | [`src/coursesched/`](src/coursesched) |
| Analysis runner, which regenerates `report/tables` and `report/figures` | [`scripts/run_analysis.py`](scripts/run_analysis.py) |

## Headline results

- **Validation dataset:** optimal objective **320**, matching the hand calculation.
- **W27:** optimal objective **0**. No conflict-group overlap and no soft request needs to be violated.
  The checker independently confirms every hard rule.
- **The optimum is far from unique.** A lexicographic tie-break (minimize weekly minutes outside prime
  time with Z held at 0) cuts those minutes from 5,390 to 1,950, and classes ending after 18:00 from
  15 to 4.
- **The base model misses section clashes.** In 6 grouped course pairs, no student could take both
  courses because their labs or discussions clash. Section-aware conflict groups remove all of them
  at no cost.
- **Custom patterns** honor two accepted requests the standard patterns cannot express
  (ECI 140B WF 14:10–16:00 and ECI 281C MW 15:00–16:50), also at Z = 0.

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
enough for the tests and the validation dataset, but the W27 model has 2,157 variables and 17,911
constraints. Install the free academic license from the Gurobi User Portal. It is personal, so never
commit `gurobi.lic` (it is git-ignored).

Without a full license, `solver="auto"` (the default in the analysis code and `--solver auto` on the
command line) solves large models with the open-source HiGHS solver instead.

**Input data.** The course workbooks are not committed because the repository is public. Copy them into
`data/raw/`:

```
data/raw/scheduling_input_W27.xlsx
data/raw/small_validation_dataset.xlsx
```

## Run

To solve one workbook and export the schedule:

```bash
PYTHONPATH=src python -m coursesched data/raw/scheduling_input_W27.xlsx --out results
```

This writes `results/scheduling_input_W27_schedule.xlsx` (Summary, Schedule, GroupOverlaps,
SoftRequests and NonPrime sheets) and a weekly calendar PNG. Options include `--solver`,
`--time-limit`, `--mip-gap`, `--overlap-weight`, `--request-weight`, `--tie-break`, `--section-aware`,
`--custom-patterns` and `--quiet`.

To produce the report's recommended schedule (section-aware groups, custom patterns for the two
accepted W27 requests, and the off-prime tie-break):

```bash
PYTHONPATH=src python -m coursesched data/raw/scheduling_input_W27.xlsx --recommended --solver auto
```

To run every analysis and regenerate the report's tables and figures (a few minutes):

```bash
PYTHONPATH=src python scripts/run_analysis.py
```

In the notebook, open `notebooks/course_scheduling.ipynb` and run all cells. Set `COURSESCHED_INPUT` to
use another workbook.

From Python:

```python
from coursesched import load_workbook, build_model_data, build_model, solve, check_schedule, meetings_from_assignment
from coursesched.model import tie_break

inputs = load_workbook("data/raw/scheduling_input_W27.xlsx")    # Step 1
data = build_model_data(inputs)                                  # Step 2 (np_rates=..., custom_patterns=..., section_aware_groups=...)
sm = build_model(data)                                           # Steps 3-4 (overlap_weight=..., request_weight=...)
solution = solve(sm, solver="auto")                              # Step 5
better = tie_break(sm, solution, solver="auto")                  # same Z, fewest off-prime minutes
check = check_schedule(inputs, meetings_from_assignment(data, better.assignment))  # Step 6 audit
```

## Tests

```bash
python -m pytest
```

The tests build a synthetic copy of the validation workbook, so they run without the course files.
Tests on the real workbooks run when the files are in `data/raw/`. The full W27 Gurobi test skips
unless a full Gurobi license is available.

## Layout

```
src/coursesched/
  patterns.py     time parsing, pattern generation, overlap and non-prime tests
  rules.py        request types: which components they apply to, which patterns violate them
  loader.py       Step 1: read, clean, validate the workbook; conflict-group pairs
  preprocess.py   Step 2: P_c, soft cases, I/H/G pairs, overlap indices, non-prime quota sets
  model.py        Steps 3-5: model, Gurobi/HiGHS solve, IIS, lexicographic tie-break
  checker.py      independent audit of any schedule
  analysis.py     Step 6: quality metrics, student conflicts, scenarios and sweeps
  report.py       result tables and Excel export
  viz.py          weekly calendars and analysis charts
  __main__.py     command-line entry point
scripts/          run_analysis.py
notebooks/        tutorial-style walkthrough
report/           report.md, tables/ (CSV), figures/ (PNG)
tests/            pytest suite
docs/             formulation and data notes
data/raw/         input workbooks (local only)
results/          generated outputs (git-ignored)
```
