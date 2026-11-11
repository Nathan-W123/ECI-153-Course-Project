"""Static figures: weekly calendars and analysis charts (matplotlib).

Styling follows one palette: categorical colors are assigned in a fixed order
(lecture, lab, discussion / first, second, third series), grids and axes are
hairlines one shade off the surface, and every chart has a single y-axis.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from .patterns import DAY_ORDER

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SHADE = "#f0efec"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")

TYPE_COLORS = {"LECTURE": SERIES[0], "LAB": SERIES[1], "DISCUSSION": SERIES[2]}
DAY_NAMES = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri"}


def _style(ax, *, grid_axis: str = "y") -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=MUTED, labelcolor=INK_SECONDARY, labelsize=9)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.6, linestyle="-")
    ax.set_axisbelow(True)
    ax.title.set_color(INK)
    ax.xaxis.label.set_color(INK_SECONDARY)
    ax.yaxis.label.set_color(INK_SECONDARY)


def _figure(width: float, height: float, nrows: int = 1, ncols: int = 1, **kwargs):
    fig, axes = plt.subplots(nrows, ncols, figsize=(width, height), squeeze=False, **kwargs)
    fig.patch.set_facecolor(SURFACE)
    return fig, axes


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
    title: str | None = None,
):
    """One weekly grid per value of ``facet``; side-by-side lanes show simultaneous meetings."""
    groups = list(schedule.dropna(subset=["days"]).groupby(facet, sort=True))
    nrows = max(1, math.ceil(len(groups) / ncols))
    fig, axes = _figure(7.5 * ncols, 5.2 * nrows, nrows, ncols)

    for ax, (name, frame) in zip(axes.flat, groups, strict=False):
        _style(ax, grid_axis="y")
        for d, day in enumerate(DAY_ORDER):
            rows = frame[frame["days"].str.contains(day)]
            lane_of, n_lanes = _lanes([(r.start_min, r.end_min, i) for i, r in rows.iterrows()])
            width = 0.92 / n_lanes
            for i, r in rows.iterrows():
                x = d + 0.04 + lane_of[i] * width
                ax.add_patch(Rectangle((x, r.start_min), width, r.end_min - r.start_min,
                                       facecolor=TYPE_COLORS.get(r.component_type, MUTED),
                                       edgecolor=SURFACE, linewidth=1.0))
                label = f"{r.course_id}\n{r.component_type[:3]} {r.section}".strip()
                ax.text(x + width / 2, r.start_min + 4, label, ha="center", va="top",
                        fontsize=max(4.5, 7.5 - n_lanes * 0.6), color="#ffffff", clip_on=True)
        ax.axhspan(*prime_window, color=SHADE, zorder=-1)
        ax.set_xlim(0, len(DAY_ORDER))
        ax.set_ylim(day_window[1], day_window[0])
        ax.set_xticks([d + 0.5 for d in range(len(DAY_ORDER))], [DAY_NAMES[d] for d in DAY_ORDER])
        hours = range(day_window[0] // 60, day_window[1] // 60 + 1)
        ax.set_yticks([h * 60 for h in hours], [f"{h:02d}:00" for h in hours])
        for d in range(1, len(DAY_ORDER)):
            ax.axvline(d, color=GRID, linewidth=0.6)
        ax.set_title(f"{name}  ({len(frame)} components)", fontsize=11)

    for ax in list(axes.flat)[len(groups):]:
        ax.set_visible(False)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in TYPE_COLORS.values()]
    fig.legend(handles, [t.title() for t in TYPE_COLORS], loc="upper center", ncol=3, frameon=False,
               labelcolor=INK_SECONDARY)
    if title:
        fig.suptitle(title, y=0.995, color=INK, fontsize=13)
    fig.text(0.5, 0.005, "Shaded band = prime time", ha="center", fontsize=9, color=MUTED)
    fig.tight_layout(rect=(0, 0.01, 1, 0.965))
    return fig


def plot_start_profile(profile: pd.DataFrame, *, prime_window: tuple[int, int] = (9, 16), title: str = ""):
    """Grouped bars: weekly meeting-minutes by start hour, one series per schedule (column order kept)."""
    fig, axes = _figure(9, 4.2)
    ax = axes[0, 0]
    _style(ax)
    n = len(profile.columns)
    width = 0.8 / n
    hours = list(profile.index)
    for k, column in enumerate(profile.columns):
        offsets = [h - 0.4 + width * (k + 0.5) for h in hours]
        ax.bar(offsets, profile[column], width=width * 0.9, color=SERIES[k], label=column)
    ax.axvspan(prime_window[0] - 0.5, prime_window[1] - 0.5, color=SHADE, zorder=-2)
    ax.text((prime_window[0] + prime_window[1]) / 2 - 0.5, ax.get_ylim()[1] * 0.97, "prime-time starts",
            ha="center", va="top", fontsize=9, color=MUTED)
    ax.set_xticks(hours, [f"{h:02d}:00" for h in hours], rotation=0)
    ax.set_xlabel("Meeting start time (hour)")
    ax.set_ylabel("Weekly meeting minutes")
    ax.set_title(title, fontsize=12, loc="left")
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    fig.tight_layout()
    return fig


def plot_lines(
    frame: pd.DataFrame,
    x: str,
    series: dict[str, str],
    *,
    xlabel: str,
    ylabel: str,
    title: str = "",
    infeasible_from: float | None = None,
    infeasible_label: str = "infeasible",
    xticks: Sequence[float] | None = None,
):
    """One or more series against a shared x on a single y-axis (2px lines, markers, end labels)."""
    fig, axes = _figure(7.5, 4.0)
    ax = axes[0, 0]
    _style(ax)
    for k, (label, column) in enumerate(series.items()):
        data = frame[[x, column]].dropna()
        ax.plot(data[x], data[column], color=SERIES[k], linewidth=2, marker="o", markersize=5, label=label)
        if len(series) > 1 and not data.empty:
            ax.annotate(label, (data[x].iloc[-1], data[column].iloc[-1]), xytext=(6, 0),
                        textcoords="offset points", va="center", fontsize=9, color=INK_SECONDARY)
    if infeasible_from is not None:
        ax.axvspan(infeasible_from, frame[x].max() + 0.025, color=SHADE, zorder=-2)
        # Bottom of the band, clear of the series' end labels near the top.
        ax.text(infeasible_from + 0.01, ax.get_ylim()[1] * 0.03, infeasible_label, va="bottom", fontsize=9,
                color=MUTED)
    if xticks is not None:
        ax.set_xticks(xticks)
    ax.set_ylim(bottom=0)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=12, loc="left")
    if len(series) > 1:
        ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9, loc="upper left")
    fig.tight_layout()
    return fig


def plot_hbars(labels: Sequence[str], values: Sequence[float], *, xlabel: str, title: str = ""):
    """Ranked horizontal bars for a single measure (one color)."""
    fig, axes = _figure(7.5, 0.42 * len(labels) + 1.4)
    ax = axes[0, 0]
    _style(ax, grid_axis="x")
    positions = range(len(labels))
    ax.barh(list(positions), list(values), height=0.6, color=SERIES[0])
    ax.set_yticks(list(positions), list(labels))
    ax.invert_yaxis()
    ax.axvline(0, color=AXIS, linewidth=0.8)
    ax.set_xlabel(xlabel)
    ax.set_title(title, fontsize=12, loc="left")
    fig.tight_layout()
    return fig
