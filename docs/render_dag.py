#!/usr/bin/env python3
"""Render a clean BenchmarkThreePaths DAG image."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = "/Users/landigf/Desktop/Code/GSoC/docs/benchmark_dag.png"

# ── Metaflow palette ────────────────────────────────────────────────
BG      = "#F0ECE3"
PURPLE  = "#7B68AE"
RED     = "#E85757"
GREEN   = "#4AA86B"
BLUE    = "#1A56DB"
WHITE   = "#FFFFFF"
DARK    = "#2D2D2D"
GREY    = "#6B6B6B"

fig, ax = plt.subplots(figsize=(11, 7))
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 11)
ax.set_ylim(0, 7)
ax.axis("off")

def box(ax, cx, cy, w, h, color, label, sublabel=None, text_color=WHITE):
    """Draw a rounded rectangle node."""
    rect = FancyBboxPatch(
        (cx - w/2, cy - h/2), w, h,
        boxstyle="round,pad=0.08",
        facecolor=color, edgecolor="none", zorder=3,
        linewidth=0
    )
    ax.add_patch(rect)
    if sublabel:
        ax.text(cx, cy + 0.13, label, ha="center", va="center",
                fontsize=11, fontweight="bold", color=text_color, zorder=4,
                fontfamily="monospace")
        ax.text(cx, cy - 0.18, sublabel, ha="center", va="center",
                fontsize=8.5, color=text_color, zorder=4, alpha=0.88,
                fontfamily="monospace")
    else:
        ax.text(cx, cy, label, ha="center", va="center",
                fontsize=12, fontweight="bold", color=text_color, zorder=4,
                fontfamily="monospace")

def arrow(ax, x0, y0, x1, y1, color=DARK):
    ax.annotate("",
        xy=(x1, y1), xytext=(x0, y0),
        arrowprops=dict(
            arrowstyle="-|>",
            color=color,
            lw=1.6,
            mutation_scale=14,
        ),
        zorder=2
    )

# ── Title ───────────────────────────────────────────────────────────
ax.text(5.5, 6.55, "BenchmarkThreePaths", ha="center", va="center",
        fontsize=16, fontweight="bold", color=DARK)
ax.text(5.5, 6.18, "Three parallel branches — same database, same question",
        ha="center", va="center", fontsize=10, color=GREY)

# ── Nodes ───────────────────────────────────────────────────────────
# start
box(ax, 5.5, 5.4, 1.6, 0.55, PURPLE, "start")

# three parallel steps
box(ax, 2.0, 3.8, 2.8, 0.80, RED,   "path_a_naive",
    "56 calls · 3,800 ms")
box(ax, 5.5, 3.8, 2.8, 0.80, GREEN, "path_c_smart_meta",
    "4 calls · 349 ms")
box(ax, 9.0, 3.8, 2.8, 0.80, BLUE,  "path_b_ui_backend",
    "2 calls · 35 ms")

# compare
box(ax, 5.5, 2.2, 1.6, 0.55, PURPLE, "compare")

# end
box(ax, 5.5, 0.9, 1.6, 0.55, PURPLE, "end")

# ── Arrows: start → three branches ──────────────────────────────────
arrow(ax, 5.5, 5.12, 2.0,  4.20)
arrow(ax, 5.5, 5.12, 5.5,  4.20)
arrow(ax, 5.5, 5.12, 9.0,  4.20)

# ── Arrows: three branches → compare ────────────────────────────────
arrow(ax, 2.0, 3.40, 5.5,  2.48)
arrow(ax, 5.5, 3.40, 5.5,  2.48)
arrow(ax, 9.0, 3.40, 5.5,  2.48)

# ── Arrows: compare → end ───────────────────────────────────────────
arrow(ax, 5.5, 1.92, 5.5,  1.18)

# ── Legend ──────────────────────────────────────────────────────────
legend_items = [
    mpatches.Patch(color=RED,   label="Path A · Naive Client API (today)"),
    mpatches.Patch(color=GREEN, label="Path C · Smart Metadata  [target]"),
    mpatches.Patch(color=BLUE,  label="Path B · UI Backend       [bonus]"),
]
ax.legend(handles=legend_items, loc="lower right",
          framealpha=0.0, fontsize=9.5,
          handlelength=1.2, handleheight=1.0,
          labelcolor=DARK)

plt.tight_layout(pad=0.4)
plt.savefig(OUT, dpi=180, bbox_inches="tight", facecolor=BG)
print(f"Saved: {OUT}")
