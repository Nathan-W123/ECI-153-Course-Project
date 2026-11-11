"""Course scheduling as a 0-1 integer linear program (ECI 153 course project).

Pipeline (mirrors the tutorial notebook):

    inputs   = load_workbook(path)              # Step 1  read + clean + validate
    data     = build_model_data(inputs)         # Step 2  sets and parameters
    sm       = build_model(data)                # Steps 3-4  variables, constraints, objective
    solution = solve(sm)                        # Step 5  Gurobi
    check    = check_schedule(inputs, meetings) # Step 6  independent audit, tables, plots
"""

from .checker import CheckReport, check_schedule
from .loader import InputData, load_workbook
from .model import SchedulingModel, Solution, build_model, diagnose_infeasibility, solve
from .preprocess import InfeasibleInputError, InputValidationError, ModelData, build_model_data
from .report import export_results, meetings_from_assignment, schedule_table

__all__ = [
    "CheckReport",
    "InfeasibleInputError",
    "InputData",
    "InputValidationError",
    "ModelData",
    "SchedulingModel",
    "Solution",
    "build_model",
    "build_model_data",
    "check_schedule",
    "diagnose_infeasibility",
    "export_results",
    "load_workbook",
    "meetings_from_assignment",
    "schedule_table",
    "solve",
]
