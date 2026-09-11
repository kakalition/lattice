"""Chart generation with shadcn/ui chart aesthetic (matplotlib)."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

from lattice.tools.file_safety import PathDeniedError, resolve_agent_path
from lattice.tools.theme import ShadcnTheme, resolve_theme

_CHART_TYPES = frozenset({"bar", "line", "area", "pie", "donut"})

# 8pt spatial scale (points → inches via /72).
PT_XS = 4.0  # icon/handle gaps
PT_SM = 8.0  # related items (title→subtitle, legend row)
PT_MD = 16.0  # card padding, header↔plot
PT_LG = 24.0  # sub-section (unused in card chrome)
PT_XL = 32.0  # major section (unused in card chrome)


def _parse_data(data_json: str) -> tuple[list[str], list[dict[str, Any]]]:
    raw = (data_json or "").strip()
    if not raw:
        raise ValueError("data_json is empty")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("data_json must be an object")
    labels = [str(x) for x in (data.get("labels") or [])]
    series_raw = data.get("series") or data.get("datasets") or []
    if not isinstance(series_raw, list) or not series_raw:
        raise ValueError("data_json.series must be a non-empty list")
    series: list[dict[str, Any]] = []
    for item in series_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("label") or "Series")
        values = item.get("values") or item.get("data") or []
        if not isinstance(values, list):
            raise ValueError(f"series '{name}' values must be a list")
        series.append({"name": name, "values": [float(v) for v in values]})
    if not series:
        raise ValueError("no valid series")
    if not labels:
        n = max(len(s["values"]) for s in series)
        labels = [str(i + 1) for i in range(n)]
    return labels, series


def _safe_chart_path(path: Path) -> Path:
    suf = path.suffix.lower()
    if suf not in {".png", ".svg", ".pdf"}:
        path = path.with_suffix(".png")
    return path


def _pt(fig_dim_in: float, points: float) -> float:
    """Convert points to a fraction of a figure dimension (width or height)."""
    return (points / 72.0) / fig_dim_in


def _apply_shadcn_style(ax: Any, theme: ShadcnTheme, *, show_y_grid: bool = True) -> None:
    ax.set_facecolor(theme.card)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(theme.border)
        ax.spines[spine].set_linewidth(1.0)
    ax.tick_params(colors=theme.muted_foreground, labelsize=9, length=0)
    if show_y_grid:
        ax.yaxis.grid(True, linestyle="--", linewidth=0.8, color=theme.border, alpha=0.9)
        ax.set_axisbelow(True)
    ax.xaxis.grid(False)
    ax.yaxis.set_tick_params(pad=6)
    ax.xaxis.set_tick_params(pad=6)


def _pad_y_limits(ax: Any) -> None:
    lo, hi = ax.get_ylim()
    if not math.isfinite(lo) or not math.isfinite(hi):
        return
    if hi <= lo:
        hi = lo + 1.0
    pad = (hi - lo) * 0.06
    ax.set_ylim(min(0.0, lo) if lo >= 0 else lo - pad, hi + pad)


def _draw_chart(
    target: Path,
    *,
    chart_type: str,
    title: str,
    description: str,
    labels: list[str],
    series: list[dict[str, Any]],
    theme: ShadcnTheme,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    from matplotlib.ticker import MaxNLocator

    target.parent.mkdir(parents=True, exist_ok=True)
    kind = chart_type.strip().lower()
    if kind not in _CHART_TYPES:
        raise ValueError(f"unsupported chart_type {chart_type!r}; use {sorted(_CHART_TYPES)}")

    has_desc = bool(description.strip())
    is_pie = kind in {"pie", "donut"}
    palette = list(theme.charts)

    # Proportional cards: square-ish for pie, wide for cartesian.
    fig_w, fig_h = (6.5, 6.6) if is_pie else (9.0, 5.0)
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=160)
    fig.patch.set_facecolor(theme.background)

    # Spatial tokens as figure fractions (8pt system).
    pad_x = _pt(fig_w, PT_MD)
    pad_y = _pt(fig_h, PT_MD)
    gap_sm = _pt(fig_h, PT_SM)
    gap_md = _pt(fig_h, PT_MD)
    title_h = _pt(fig_h, 18.0)
    desc_h = _pt(fig_h, 12.0)
    legend_h = _pt(fig_h, 11.0)

    card = FancyBboxPatch(
        (pad_x * 0.35, pad_y * 0.35),
        1.0 - pad_x * 0.7,
        1.0 - pad_y * 0.7,
        boxstyle="round,pad=0.006,rounding_size=0.014",
        linewidth=1.1,
        edgecolor=theme.border,
        facecolor=theme.card,
        transform=fig.transFigure,
        zorder=-1,
        clip_on=False,
    )
    fig.add_artist(card)

    # Content box inside card padding.
    left = pad_x
    right = 1.0 - pad_x
    top = 1.0 - pad_y
    bottom = pad_y
    # Y labels need a left gutter; X labels need a small SM strip above card pad.
    y_label_gutter = _pt(fig_w, 22.0)
    x_label_gutter = _pt(fig_h, PT_SM)

    # Header group (proximity: SM between related lines).
    y = top
    fig.text(
        left,
        y,
        title or "Chart",
        fontsize=14,
        fontweight="bold",
        color=theme.foreground,
        va="top",
        ha="left",
        transform=fig.transFigure,
    )
    y -= title_h + gap_sm
    if has_desc:
        fig.text(
            left,
            y,
            description.strip(),
            fontsize=9,
            color=theme.muted_foreground,
            va="top",
            ha="left",
            transform=fig.transFigure,
        )
        y -= desc_h + gap_sm

    # Legend sits in the header group (related chrome).
    # SM (not MD) before the plot — same component, not a new section.
    legend_anchor_y = y
    if is_pie:
        plot_bottom = bottom + legend_h + gap_sm
        plot_top = y - gap_sm
        plot_left = left
        plot_right = right
    else:
        y -= legend_h + gap_sm
        plot_top = y
        plot_bottom = bottom + x_label_gutter
        plot_left = left + y_label_gutter
        plot_right = right - _pt(fig_w, PT_SM)

    plot_h = max(0.35, plot_top - plot_bottom)
    plot_w = max(0.35, plot_right - plot_left)
    # Keep pie axes square within the available band.
    if is_pie:
        side = min(plot_w, plot_h * (fig_h / fig_w))
        plot_left = left + (plot_w - side) / 2
        ax = fig.add_axes([plot_left, plot_bottom, side, plot_h if side >= plot_h else side])
        # Re-center vertically in the band if square is shorter than the band.
        if side < plot_h:
            ax.set_position(
                [
                    plot_left,
                    plot_bottom + (plot_h - side) / 2,
                    side,
                    side,
                ]
            )
    else:
        ax = fig.add_axes([plot_left, plot_bottom, plot_w, plot_h])

    ax.set_facecolor(theme.card)
    x = list(range(len(labels)))

    if is_pie:
        pie_labels = list(labels)
        values = series[0]["values"]
        if len(values) != len(pie_labels):
            if len(series) > 1 and all(len(s["values"]) == 1 for s in series):
                pie_labels = [s["name"] for s in series]
                values = [s["values"][0] for s in series]
            else:
                n = min(len(values), len(pie_labels))
                values, pie_labels = values[:n], pie_labels[:n]
        wedges, _ = ax.pie(
            values,
            labels=None,
            colors=[palette[i % len(palette)] for i in range(len(values))],
            startangle=90,
            radius=1.0,
            wedgeprops={
                "width": 0.58 if kind == "donut" else 1.0,
                "edgecolor": theme.card,
                "linewidth": 2.5,
            },
        )
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect("equal")
        fig.legend(
            wedges,
            pie_labels,
            loc="upper center",
            bbox_to_anchor=(0.5, bottom + legend_h),
            bbox_transform=fig.transFigure,
            ncol=min(4, max(1, len(pie_labels))),
            frameon=False,
            fontsize=9,
            labelcolor=theme.muted_foreground,
            handlelength=1.0,
            handletextpad=PT_XS / 10.0,
            columnspacing=1.1,
            borderaxespad=0.0,
        )
    else:
        _apply_shadcn_style(ax, theme)
        if kind == "bar":
            n_series = len(series)
            width = min(0.86 / max(n_series, 1), 0.40)
            gap = width * 0.08
            for i, s in enumerate(series):
                vals = s["values"][: len(labels)]
                if len(vals) < len(labels):
                    vals = vals + [0.0] * (len(labels) - len(vals))
                offsets = [xi + (i - (n_series - 1) / 2) * (width + gap) for xi in x]
                bars = ax.bar(
                    offsets,
                    vals,
                    width=width,
                    color=palette[i % len(palette)],
                    edgecolor="none",
                    zorder=3,
                    label=s["name"],
                )
                for b in bars:
                    b.set_linewidth(0)
            ax.set_xlim(-0.5, len(labels) - 0.5)
        elif kind in {"line", "area"}:
            for i, s in enumerate(series):
                vals = s["values"][: len(labels)]
                if len(vals) < len(labels):
                    vals = vals + [math.nan] * (len(labels) - len(vals))
                color = palette[i % len(palette)]
                ax.plot(
                    x[: len(vals)],
                    vals,
                    color=color,
                    linewidth=2.4,
                    marker="o",
                    markersize=5.5,
                    markerfacecolor=theme.card,
                    markeredgewidth=2,
                    markeredgecolor=color,
                    label=s["name"],
                    zorder=4,
                )
                if kind == "area":
                    ax.fill_between(
                        x[: len(vals)],
                        vals,
                        color=color,
                        alpha=0.14,
                        zorder=2,
                    )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, color=theme.muted_foreground)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune=None))
        _pad_y_limits(ax)
        for label in (*ax.get_xticklabels(), *ax.get_yticklabels()):
            label.set_clip_on(False)

        handles, legend_labels = ax.get_legend_handles_labels()
        fig.legend(
            handles,
            legend_labels,
            loc="upper left",
            bbox_to_anchor=(left, legend_anchor_y),
            bbox_transform=fig.transFigure,
            ncol=min(4, max(1, len(legend_labels))),
            frameon=False,
            fontsize=9,
            labelcolor=theme.muted_foreground,
            handlelength=1.1,
            handletextpad=PT_XS / 10.0,
            columnspacing=1.15,
            borderaxespad=0.0,
        )

    fig.savefig(
        str(target),
        facecolor=fig.get_facecolor(),
        edgecolor="none",
        bbox_inches=None,
        pad_inches=0.0,
    )
    plt.close(fig)


async def generate_chart(
    path: str,
    chart_type: str,
    title: str,
    data_json: str,
    *,
    description: str = "",
    theme: str = "light",
    workspace: Path,
    home: Path,
) -> str:
    """Render a shadcn-styled chart to png/svg/pdf under the workspace."""
    try:
        target = _safe_chart_path(resolve_agent_path(path, workspace, home=home))
    except PathDeniedError as exc:
        return f"error: {exc}"

    try:
        labels, series = _parse_data(data_json)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return f"error: {exc}"

    theme_obj = resolve_theme(theme)
    kind = chart_type.strip().lower()

    def _run() -> str:
        try:
            _draw_chart(
                target,
                chart_type=kind,
                title=title,
                description=description,
                labels=labels,
                series=series,
                theme=theme_obj,
            )
        except Exception as exc:  # noqa: BLE001
            return f"chart error: {exc}"
        try:
            rel = str(target.relative_to(workspace.resolve()))
        except ValueError:
            rel = str(target)
        return f"wrote chart → {rel} ({target.stat().st_size} bytes)"

    return await asyncio.to_thread(_run)
