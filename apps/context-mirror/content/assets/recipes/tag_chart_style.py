"""tag_chart_style.py — TAG-branded data visualization defaults.

Drop-in module that any matplotlib chart can import for instant TAG visuals.
Maps the brand palette and unity gradient onto chart styling, sets typography
to Arial (Open Sauce Sans is licensed under OFL but Arial is the safest
cross-platform default for charts that get embedded in PowerPoint/Word).

Usage:
    import matplotlib.pyplot as plt
    from tag_chart_style import apply, finalize, tag_palette

    apply()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(["Q1","Q2","Q3","Q4"], [12, 18, 22, 27], color=tag_palette()[:4])
    ax.set_title("Quarterly results")
    finalize(fig, source="Source: TAG internal data (FY26)")
    fig.savefig("chart.png", dpi=200, bbox_inches="tight")
"""
from __future__ import annotations

# Canonical TAG colours (do not invent new ones).
PRIMARY_500   = "#1C304B"   # body / axis labels
PRIMARY_300   = "#5C6B82"   # gridlines / secondary text
TURQUOISE     = "#5CB8B2"   # primary accent / first series
GREY_100      = "#F5F6F8"
GREY_300      = "#C7CCD3"

UNITY_GRADIENT = [
    ("#2DBFB8", 0.00),
    ("#1A7BAD", 0.22),
    ("#6B2D8B", 0.44),
    ("#E30613", 0.63),
    ("#F05A28", 0.81),
    ("#F9B233", 1.00),
]


def tag_palette() -> list[str]:
    """Return an ordered list of the 6 unity-gradient anchor colours.

    Use as the default cycle for categorical charts. Drop the gradient anchor
    that conflicts with your background.
    """
    return [c for c, _ in UNITY_GRADIENT]


def apply():
    """Set sensible matplotlib rcParams for TAG charts."""
    import matplotlib as mpl
    mpl.rcParams.update({
        "font.family":          "Arial",
        "font.size":            10,
        "axes.titlesize":       13,
        "axes.titleweight":     "bold",
        "axes.titlecolor":      PRIMARY_500,
        "axes.labelsize":       10,
        "axes.labelcolor":      PRIMARY_500,
        "axes.edgecolor":       GREY_300,
        "axes.linewidth":       0.8,
        "axes.grid":            True,
        "axes.grid.axis":       "y",
        "grid.color":           GREY_100,
        "grid.linewidth":       0.8,
        "xtick.color":          PRIMARY_300,
        "ytick.color":          PRIMARY_300,
        "xtick.labelsize":      9,
        "ytick.labelsize":      9,
        "axes.spines.top":      False,
        "axes.spines.right":    False,
        "legend.frameon":       False,
        "legend.fontsize":      9,
        "figure.facecolor":     "white",
        "axes.facecolor":       "white",
        "axes.prop_cycle":      mpl.cycler(color=tag_palette()),
    })


def add_unity_bar(fig, height_in: float = 0.06, gap_in: float = 0.04):
    """Draw the canonical unity-gradient bar at the bottom of the figure.

    The bar always sits at the bottom (never the top, never resized in height,
    never recoloured), per the TAG brand rule.
    """
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    import numpy as np

    fig_w_in, fig_h_in = fig.get_size_inches()
    bar_h_frac = height_in / fig_h_in
    gap_frac   = gap_in / fig_h_in

    # Reserve space at the bottom so the bar doesn't overlap labels.
    fig.subplots_adjust(bottom=fig.subplotpars.bottom + bar_h_frac + gap_frac)

    bar_ax = fig.add_axes([0.0, 0.0, 1.0, bar_h_frac])
    bar_ax.set_xticks([]); bar_ax.set_yticks([])
    for spine in bar_ax.spines.values():
        spine.set_visible(False)

    cmap = mcolors.LinearSegmentedColormap.from_list("tag-unity", [c for c, _ in UNITY_GRADIENT])
    grad = np.linspace(0, 1, 1024).reshape(1, -1)
    bar_ax.imshow(grad, aspect="auto", cmap=cmap, extent=(0, 1, 0, 1))


def finalize(fig, source: str | None = None) -> None:
    """Apply the TAG footer treatment: source line + unity bar."""
    if source:
        fig.text(0.01, 0.02, source, fontsize=8, color=PRIMARY_300, ha="left", va="bottom")
    add_unity_bar(fig)


# Convenience: a Plotly version of the same palette.
def plotly_layout() -> dict:
    """Return a Plotly layout dict that approximates apply()."""
    return {
        "font":            {"family": "Arial", "size": 12, "color": PRIMARY_500},
        "title":           {"font": {"size": 16, "color": PRIMARY_500}},
        "plot_bgcolor":    "white",
        "paper_bgcolor":   "white",
        "colorway":        tag_palette(),
        "xaxis":           {"gridcolor": GREY_100, "linecolor": GREY_300, "color": PRIMARY_300},
        "yaxis":           {"gridcolor": GREY_100, "linecolor": GREY_300, "color": PRIMARY_300},
        "legend":          {"orientation": "h", "yanchor": "bottom", "y": -0.25, "xanchor": "center", "x": 0.5},
    }


if __name__ == "__main__":
    # Smoke test
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    apply()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(["Q1","Q2","Q3","Q4"], [12, 18, 22, 27])
    ax.set_title("Sample quarterly chart")
    ax.set_ylabel("Revenue (millions)")
    finalize(fig, source="Source: TAG internal data (illustrative)")
    fig.savefig("/tmp/_tag_chart_smoke.png", dpi=200, bbox_inches="tight")
    print("smoke test ok: /tmp/_tag_chart_smoke.png")
