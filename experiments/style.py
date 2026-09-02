"""Shared plotting style for every figure in the paper and the report.

The palette was chosen by running the colour validator, not by eye: blue / rust /
teal passes the lightness band, chroma floor, CVD separation (worst adjacent pair
dE 9.2 under deuteranopia), normal-vision separation and 3:1 contrast against a
white surface. Because these figures are printed - and a report is frequently
photocopied in greyscale - every categorical encoding is doubled with a hatch
pattern or a marker shape, so no reading depends on hue alone.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# validated categorical order - assign in this order, never cycle
BLUE = "#255fa0"
RUST = "#a8451c"
TEAL = "#0d7d63"
CATEGORICAL = (BLUE, RUST, TEAL)

# secondary encoding for greyscale / CVD readers
HATCH = ("", "///", "...")
MARKERS = ("o", "s", "^")

INK = "#1a1a1a"
INK_2 = "#555555"
GRID = "#d8d8d8"
SURFACE = "#ffffff"

# status colours are reserved and never reused as a fourth series
GOOD = "#0d7d63"
BAD = "#a8451c"


def apply() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
        "font.size": 9.5,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 26,
        "axes.labelsize": 9.5,
        "axes.labelcolor": INK,
        "axes.edgecolor": "#999999",
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "grid.alpha": 0.9,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.frameon": False,
        "legend.fontsize": 8.5,
        "lines.linewidth": 2.0,
        "lines.markersize": 6,
    })


def legend_top(ax, ncol: int = 3) -> None:
    """Legends go above the plot area, never inside it.

    Every one of these figures had its legend land on top of the data when
    matplotlib placed it automatically - and a legend covering a mark is worse
    than no legend, because the reader cannot tell what is hidden."""
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.005), ncol=ncol,
              frameon=False, borderaxespad=0, handlelength=1.6,
              columnspacing=1.5, handletextpad=0.6)


def caption(ax, text: str, y: float = -0.30) -> None:
    """One line under the axes stating N, or a caveat the reader needs. Figures
    that hide their N are the ones nobody can check."""
    ax.annotate(text, xy=(0, y), xycoords="axes fraction",
                fontsize=8, color=INK_2, ha="left", va="top")


def finish(fig, path, note: str | None = None) -> str:
    if note:
        fig.text(0.005, 0.005, note, fontsize=7.5, color=INK_2, ha="left", va="bottom")
    fig.savefig(path)
    plt.close(fig)
    return str(path)
