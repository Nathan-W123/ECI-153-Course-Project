# Data notes

Findings from auditing the two input workbooks. Every check below runs automatically in
`load_workbook` (`inputs.validation`) or `build_model_data`. Both workbooks pass all the
automated checks.

## Structure

| | `scheduling_input_W27.xlsx` | `small_validation_dataset.xlsx` |
|---|---|---|
| Sheets | Components, Requests, ConflictGroups, Settings, CourseSummary | + README, **FixedSchedule** |
| Active components | 64 (38 lectures, 20 labs, 6 discussions) | 7 |
| Active requests | 14 of 22 (10 hard, 4 soft) | 1 soft + 4 FixedSchedule blocks |
| Conflict groups | 9 groups, 46 rows (weights 1, 3, 5) | 1 |
| Term key in Settings | `term` | `Quarter` (normalized to `term`) |

## Things the tutorial code does not handle

1. **`FixedSchedule` sheet** (validation workbook only). The rows are hard blocks targeting a conflict
   group. Without them the validation answer is wrong.
2. **Settings key name.** The tutorial reads `settings['term']`, which fails on the validation workbook.
3. **Hard "PREFER" requests.** REQ-W27-009 (`PREFER_DAYS`) and REQ-W27-014 (`PREFER_TIME_WINDOW`) are hard.
   Hardness must come from the `hard` column.
4. **Soft-case names.** The tutorial suggests `request_id::component_id`, but Gurobi cannot write `:` into
   LP/MPS files and falls back to anonymous names. This code uses `request_id@component_id`.

## Validation dataset: known answer

FixedSchedule blocks MWF 08:00–10:00 and 11:00–20:00, and TR 08:00–12:30 and 14:10–20:00, for the three
Water lectures. The only LEC3 pattern left is **MWF 10:00–10:50**, so:

- all three lecture pairs overlap: 3 × 100 × 1 = **300**
- ECI 144's soft "prefer TR" request cannot be met: 1 × 20 × 1 = **20**
- **optimal objective = 320**

This is the regression test in `tests/test_model.py`. If overlap checks use `<=` instead of `<`, the
10:00 slot is wrongly blocked and the model becomes infeasible.

## W27 observations

- **8 components are pinned** to a single pattern by `FIXED_PATTERN` requests: ECI-130-DIS-01,
  ECI-149L-LAB-01, ECI-149N-DIS-01, ECI-161-DIS-01, ECI-193-LAB-ENV, ECI-193-LAB-WATER, ECI-212A-LEC,
  ECI-292B-LEC.
- **Only 4 soft requests are active** (REQ-W27-001, -010, -012, -013). Conflict-group overlaps dominate
  the objective.
- **Instructors link courses across departments.** The five ECI 193 discipline labs are taught by
  instructors who also teach other courses, and ECI 296 is co-taught (`A/B` in the data). That gives 53
  instructor pairs in total.
- **ECI 149L labs have no `parent_component_id`.** Only the shared instructor keeps them away from
  ECI 149N.
- **Overlapping groups.** ECI 130/133/134/135 belong to both STRUCTURE_LECTURES (weight 1) and
  STRUCTURE_UG_WITH_SENIOR_DESIGN (weight 3), and ECI 273 belongs to three groups. Penalties add up.
- **Small quota groups.** $\lceil 0.30 \times 1 \rceil = 1$ forces ECI-16-LEC (Transportation) and
  ECI-193-LEC (Environmental) to be non-prime. Construction's 2 regular components need 1 non-prime.
- **`slot_minutes` (10)** is in Settings but unused. Overlaps are detected exactly from intervals.
- **Large components.** ENG-35-DIS-01 inherits its lecture's enrollment of 190 and counts as large.

## Known modelling limits noted in the data ("V1 cannot encode …")

These inactive requests and notes are good candidates for model extensions:

| Item | What the data says | Status |
|---|---|---|
| REQ-W27-022, ECI 140B | an accepted WF 2–4 PM pattern is not a LEC4 pattern | **Handled** by the custom-pattern extension (WF 14:10–16:00) |
| REQ-W27-009/010, ECI 281C | the approved 3–5 PM start is non-standard | **Handled** by the custom-pattern extension (MW 15:00–16:50) |
| REQ-W27-015 | ECI 115 lecture and lab should form two continuous blocks (adjacency) | Future work |
| REQ-W27-018 | ECI 193's 2 lecture hours are approximated as one 110-minute block | Future work |
| ECI 191C, 295A | weekly-meeting structure inferred from the final schedule | Kept as in the data |

See `analysis.w27_v2_scenario` and Section 4.3 of the report.
