"""Spider chart (radar chart) generation for transmutation profiles.

Generates a matplotlib radar chart as a PNG BLOB showing dimension scores
with capacity-level framing and a neutral blue color scheme.
"""

import io
from typing import Any

import matplotlib
matplotlib.use("Agg")  # headless backend

import matplotlib.pyplot as plt
import numpy as np


# Capacity-level labels for axis ticks (replacing raw 1-5 scores)
CAPACITY_LABELS = ["", "Emerging", "Developing", "Moderate", "Strong", "Advanced"]

# Blue gradient colors
FILL_COLOR = "#4A90D9"
LINE_COLOR = "#2C5F9E"
FILL_ALPHA = 0.25
BG_COLOR = "#FAFBFD"
GRID_COLOR = "#D0D8E8"
LABEL_COLOR = "#2C3E50"

# Short display names for dimensions
DIMENSION_LABELS = {
    "Emotional Awareness": "Emotional",
    "Social Awareness": "Social",
    "Meta-Cognitive Awareness": "Meta-Cognitive",
    "Physical Awareness": "Physical",
    "Spatial Awareness": "Spatial",
    "Temporal Awareness": "Temporal",
    "Interoceptive Awareness": "Interoceptive",
    "Environmental Awareness": "Environmental",
    "Cognitive Awareness": "Cognitive",
    "Mindfulness": "Mindfulness",
    "Flow Awareness": "Flow",
    "Transmutation Capacity": "Transmutation",
    "Systemic Awareness": "Systemic",
}


def generate_spider_chart(
    dimension_scores: dict[str, Any],
    max_score: float = 5.0,
) -> bytes:
    """Generate a radar chart PNG from dimension scores.

    Args:
        dimension_scores: Dict of dimension name -> {"score": float, ...}
        max_score: Maximum possible score (default 5.0 for Likert scale)

    Returns:
        PNG image as bytes
    """
    # Extract dimensions that have data
    dims = []
    scores = []
    is_transmutarian = []

    for dim_name, dim_data in dimension_scores.items():
        if dim_data.get("insufficient_data"):
            continue
        dims.append(DIMENSION_LABELS.get(dim_name, dim_name))
        scores.append(dim_data.get("score", 0.0))
        is_transmutarian.append(
            dim_name in ("Flow Awareness", "Transmutation Capacity", "Systemic Awareness")
        )

    if not dims:
        return _generate_empty_chart()

    n = len(dims)

    # Compute angles for each axis
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    # Close the polygon
    scores_closed = scores + [scores[0]]
    angles_closed = angles + [angles[0]]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Draw the filled area
    ax.fill(angles_closed, scores_closed, color=FILL_COLOR, alpha=FILL_ALPHA)
    ax.plot(angles_closed, scores_closed, color=LINE_COLOR, linewidth=2)

    # Draw score points
    for i, (angle, score) in enumerate(zip(angles, scores)):
        marker_color = "#E67E22" if is_transmutarian[i] else LINE_COLOR
        ax.plot(angle, score, "o", color=marker_color, markersize=6, zorder=5)

    # Configure axes
    ax.set_xticks(angles)
    ax.set_xticklabels(dims, fontsize=9, color=LABEL_COLOR)

    # Capacity-level ticks on radial axis
    ax.set_ylim(0, max_score)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_yticklabels(CAPACITY_LABELS[1:], fontsize=7, color=LABEL_COLOR, alpha=0.7)

    # Grid styling
    ax.spines["polar"].set_color(GRID_COLOR)
    ax.grid(color=GRID_COLOR, linewidth=0.5)

    # Title
    ax.set_title(
        "Awareness & Transmutation Capacity Profile",
        fontsize=13,
        fontweight="bold",
        color=LABEL_COLOR,
        pad=20,
    )

    # Legend for transmutarian dimensions
    if any(is_transmutarian):
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], marker="o", color="w", markerfacecolor=LINE_COLOR, markersize=8, label="Awareness"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#E67E22", markersize=8, label="Transmutarian"),
        ]
        ax.legend(handles=legend_elements, loc="upper right", bbox_to_anchor=(1.15, 1.1), fontsize=8)

    plt.tight_layout()

    # Save to bytes
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _generate_empty_chart() -> bytes:
    """Generate a placeholder chart when no data is available."""
    fig, ax = plt.subplots(figsize=(8, 8))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.text(
        0.5, 0.5,
        "Insufficient data for profile chart",
        ha="center", va="center",
        fontsize=14, color=LABEL_COLOR, alpha=0.6,
    )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
