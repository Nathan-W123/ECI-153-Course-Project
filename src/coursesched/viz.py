"""Weekly calendar plots of a schedule table (see ``report.schedule_table``)."""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from .patterns import DAY_ORDER

TYPE_COLORS = {"LECTURE": "#4C72B0", "DISCUSSION": "#55A868", "LAB": "#DD8452"}
DAY_NAMES = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri"}


def _lanes(intervals: list[tuple[int, int, int]]) -> tuple[dict[int, int], int]:
    """Greedy interval partitioning: row index -> lane, and the number of lanes used."""
    lane_end: list[int] = []
    lane_of: dict[int, int] = {}
    for start, end, idx in sorted(intervals):
        for lane, last_end in enumerate(lane_end):
            if last_end <= start:
                lane_end[lane] = end
                lane_of[idx] = lane
                break
        else:
            lane_of[idx] = len(lane_end)
            lane_end.append(end)
    return lane_of, max(1, len(lane_end))


def plot_weekly(
    schedule: pd.DataFrame,
    facet: str = "department",
    *,
    prime_window: tuple[int, int] = (9 * 60, 16 * 60),
    day_window: tuple[int, int] = (8 * 60, 20 * 60),
    ncols: int = 2,
):
    """One weekly grid per value of ``facet``; side-by-side lanes show simultaneous meetings."""
    groups = list(schedule.dropna(subset=["days"]).groupby(facet, sort=True))
    nrows = max(1, math.ceil(len(groups) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.5 * ncols, 5.2 * nrows), squeeze=False)

    for ax, (name, frame) in zip(axes.flat, groups, strict=False):
        for d, day in enumerate(DAY_ORDER):
            rows = frame[frame["days"].str.contains(day)]
            lane_of, n_lanes = _lanes([(r.start_min, r.end_min, i) for i, r in rows.iterrows()])
            width = 0.92 / n_lanes
            for i, r in rows.iterrows():
                x = d + 0.04 + lane_of[i] * width
                ax.add_patch(Rectangle((x, r.start_min), width, r.end_min - r.start_min,
                                       facecolor=TYPE_COLORS.get(r.component_type, "#8C8C8C"),
                                       edgecolor="white", linewidth=0.8, alpha=0.9))
                label = f"{r.course_id}\n{r.component_type[:3]} {r.section}".strip()
                ax.text(x + width / 2, r.start_min + 4, label, ha="center", va="top",
                        fontsize=max(4.5, 7.5 - n_lanes * 0.6), color="white", clip_on=True)
        ax.axhspan(*prime_window, color="#F2F2F2", zorder=-1)
        ax.set_xlim(0, len(DAY_ORDER))
        ax.set_ylim(day_window[1], day_window[0])
        ax.set_xticks([d + 0.5 for d in range(len(DAY_ORDER))], [DAY_NAMES[d] for d in DAY_ORDER])
        hours = range(day_window[0] // 60, day_window[1] // 60 + 1)
        ax.set_yticks([h * 60 for h in hours], [f"{h:02d}:00" for h in hours])
        for d in range(1, len(DAY_ORDER)):
            ax.axvline(d, color="#BBBBBB", linewidth=0.6)
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.5)
        ax.set_title(f"{name}  ({len(frame)} components)", fontsize=11)

    for ax in list(axes.flat)[len(groups):]:
        ax.set_visible(False)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in TYPE_COLORS.values()]
    fig.legend(handles, [t.title() for t in TYPE_COLORS], loc="upper center", ncol=3, frameon=False)
    fig.text(0.5, 0.005, "Shaded band = prime time", ha="center", fontsize=9, color="#666666")
    fig.tight_layout(rect=(0, 0.01, 1, 0.97))
    return fig
