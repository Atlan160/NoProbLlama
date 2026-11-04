"""Zeichnet den Kontext-Test: Prompt- und Antworttempo über der Kontextgröße, in zwei Plots mit geteilter X-Achse."""

import math
import re

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
GUIDE = "#a8a7a2"
PROMPT_COLOR = "#2a78d6"
ANSWER_COLOR = "#eb6834"
REPEAT_COLOR = "#1f8a70"
AXIS_STEPS = 4


def compact_tokens(value):
    """Vielfache von 1024 wie 16384 werden zu 16K, andere Werte wie 40192 zu 40K."""
    if value < 1024:
        return str(value)
    return f"{value // 1024}K" if value % 1024 == 0 else f"{value / 1000:.0f}K"


def compact_number(value):
    if value >= 1000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return text.replace(".", ",") + "K"
    return f"{value:g}".replace(".", ",")


def german_number(value, digits=1):
    text = f"{value:,.{digits}f}"
    return text.replace(",", "§").replace(".", ",").replace("§", ".")


def nice_top(highest):
    """Runde Obergrenze einer Achse, die sich in AXIS_STEPS glatte Stufen teilen lässt."""
    if highest <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(highest / AXIS_STEPS))
    for factor in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        step = factor * magnitude
        if step * AXIS_STEPS >= highest * 1.05:
            return step * AXIS_STEPS
    return highest * 1.05


def _measured(points, key):
    return [(p["ctx"], p[key]) for p in points if isinstance(p.get(key), (int, float)) and p[key] > 0]


def _panel_title(label, series):
    """Nennt die Messgröße und wie stark das Tempo vom besten Wert bis zum größten Kontext fällt."""
    if not series:
        return f"{label}: nicht gemessen"
    best = max(value for _, value in series)
    last = series[-1][1]
    if len(series) < 2 or last >= best * 0.97:
        return f"{label}: {'stabil bei ' if len(series) > 1 else ''}{german_number(last)} Tok/s"
    return f"{label}: {german_number(best)} → {german_number(last)} Tok/s (−{(1 - last / best) * 100:.0f} %)"


def _draw_panel(axis, label, series, color, marker, failed, compact, connect=True):
    axis.set_facecolor("none")
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(GRID)
    axis.tick_params(colors=TEXT_SECONDARY, length=0, labelsize=8 if compact else 9)
    axis.set_title(_panel_title(label, series), loc="left", fontsize=9 if compact else 11, color=TEXT_PRIMARY, pad=4 if compact else 6)
    if series:
        # Der Ring in Hintergrundfarbe hält die Marker auf der Linie lesbar.
        axis.plot(*zip(*series), color=color, marker=marker, linewidth=2 if connect else 0, markersize=8 if connect else 11, markeredgecolor=SURFACE, markeredgewidth=2,
                  solid_capstyle="round", solid_joinstyle="round", clip_on=False, zorder=3)
    if failed:
        axis.plot(failed, [0] * len(failed), linestyle="none", marker="x", markersize=8, markeredgewidth=2, color=TEXT_SECONDARY,
                  label="Fehlgeschlagen", clip_on=False, zorder=4)
    top = nice_top(max((value for _, value in series), default=0))
    ticks = [top * step / AXIS_STEPS for step in range(AXIS_STEPS + 1)]
    axis.set_ylim(0, top)
    axis.set_yticks(ticks)
    axis.set_yticklabels([compact_number(value) for value in ticks])
    axis.grid(axis="y", color=GRID, linewidth=1)
    axis.set_axisbelow(True)
    axis.set_ylabel("Tok/s", color=TEXT_SECONDARY, fontsize=8 if compact else 9)


def _clear(figure):
    for axis in figure.axes:  # Beim Leeren setzt matplotlib die Grenzen zurück, das verträgt der Log-Maßstab nicht.
        axis.set_xscale("linear")
    figure.clear()
    figure.set_facecolor(SURFACE)


def draw_sweep(figure, sweep, compact=False):
    """Zeichnet einen Test in die Figure und liefert die Achsen für den Hover-Layer."""
    _clear(figure)
    top = figure.add_subplot(2, 1, 1)
    bottom = figure.add_subplot(2, 1, 2, sharex=top)
    top.set_zorder(2)  # Die Hover-Beschriftung hängt am oberen Plot und darf über den unteren reichen.
    points = sweep.get("points", [])
    prompt = _measured(points, "prompt_tps")
    answer = _measured(points, "tps")
    failed = [p["requested"] for p in points if not p.get("tps") and not p.get("prompt_tps")]
    axes = {"prompt": top, "answer": bottom, "points": points}

    if not compact:  # Im Fenster stehen Modell und Zeit schon in der Auswahl darüber.
        title = f"{sweep.get('model', '')}   ·   {sweep.get('time', '')}"
        if not sweep.get("complete", True):
            title += "   ·   unvollständig"
        figure.suptitle(title, x=0.01, ha="left", fontsize=12, color=TEXT_PRIMARY)
    _draw_panel(top, "Prompt einlesen", prompt, PROMPT_COLOR, "o", failed, compact)
    _draw_panel(bottom, "Antwort erzeugen", answer, ANSWER_COLOR, "s", failed, compact)
    top.tick_params(labelbottom=False)
    if not points:
        bottom.set_xticks([])
        return axes

    # Ein logarithmischer Maßstab, damit kleine Kontexte neben sehr großen lesbar bleiben.
    tested = sorted({p["ctx"] for p in points if p.get("ctx")} | set(failed))
    bottom.set_xscale("log", base=2)
    room = 1.08 if len(tested) > 1 else 1.5
    bottom.set_xlim(tested[0] / room, tested[-1] * room)
    bottom.set_xticks(tested)
    bottom.set_xticklabels([compact_tokens(value) for value in tested])
    bottom.minorticks_off()
    bottom.set_xlabel("Kontextgröße in Tokens, logarithmisch", color=TEXT_SECONDARY, fontsize=8 if compact else 9)
    if failed:
        bottom.legend(loc="upper right", frameon=False, fontsize=8, labelcolor=TEXT_PRIMARY)

    limit = sweep.get("fit_limit")
    if limit and tested[0] <= limit <= tested[-1]:
        for axis in (top, bottom):
            axis.axvline(limit, color=GUIDE, linewidth=1, zorder=1)
        top.text(limit, 0.98, " VRAM voll (Schätzung)", color=TEXT_SECONDARY, fontsize=8, va="top", ha="left", transform=top.get_xaxis_transform())
    return axes


def _param_title(label, series, names):
    if not series:
        return f"{label}: nicht gemessen"
    return f"{label}: am schnellsten {names[max(series, key=lambda item: item[1])[0]]}"  # Die Zahlen stehen an den Werten.


def _change_title(label, series):
    """Für den Vergleich zweier Messungen: Der Unterschied steht in Zahlen da, weil die Achse nicht bei 0 beginnt."""
    if len(series) != 2 or not series[0][1]:
        return None
    first, last = series[0][1], series[1][1]
    change = german_number((last / first - 1) * 100).replace("-", "−")
    return f"{label}: {german_number(first)} → {german_number(last)} Tok/s ({'+' if last >= first else ''}{change} %)"


def bar_limits(values):
    """Achse für Balken: Sie beginnt unter dem kleinsten Wert, damit auch kleine Unterschiede sichtbar werden."""
    low, high = min(values), max(values)
    if len(values) < 2:
        return 0, high * 1.25
    span = max(high - low, high * 0.02)
    return max(0, low - span), high + span * 0.4


def _draw_bars(axis, series, color, marker, failed, compact, chosen):
    """Balken für Werte ohne Reihenfolge, jeweils mit dem Zahlenwert darüber."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    axis.set_facecolor("none")
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(GRID)
    axis.tick_params(colors=TEXT_SECONDARY, length=0, labelsize=8 if compact else 9)
    axis.grid(axis="y", color=GRID, linewidth=1)
    axis.set_axisbelow(True)
    bottom = 0
    if series:
        bottom, top = bar_limits([value for _, value in series])
        axis.set_ylim(bottom, top)
        for position, value in series:
            axis.bar(position, value - bottom, bottom=bottom, width=0.5, color=color, alpha=1 if chosen in (None, position) else 0.45, zorder=3)
            axis.annotate(german_number(value), (position, value), xytext=(0, 4), textcoords="offset points", ha="center", va="bottom", fontsize=9 if compact else 11,
                          color=TEXT_PRIMARY, zorder=4)
        digits = 0 if top - bottom >= 4 else 1
        axis.yaxis.set_major_locator(MaxNLocator(nbins=4))
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: german_number(value, digits)))
    if failed:
        axis.plot(failed, [bottom] * len(failed), linestyle="none", marker="x", markersize=8, markeredgewidth=2, color=TEXT_SECONDARY, label="Fehlgeschlagen", clip_on=False, zorder=4)
    note = f"Tok/s, Achse ab {german_number(bottom, 0 if bottom >= 100 else 1)}" if bottom > 0 else "Tok/s"
    axis.set_ylabel(note, color=TEXT_SECONDARY, fontsize=8 if compact else 9)


def draw_param(figure, step, compact=False):
    """Zeichnet einen Parameter-Schritt: pro Messgröße ein Plot, alle mit denselben Werten auf der X-Achse."""
    _clear(figure)
    points = step.get("points", [])
    names = [str(point.get("label", point.get("value", ""))) for point in points]
    panels = [("Prompt einlesen", "prompt_tps", PROMPT_COLOR, "o"), ("Antwort, neuer Text", "tps", ANSWER_COLOR, "s")]
    if any(point.get("repeat_tps") for point in points):
        panels.append(("Antwort, wiederholter Text", "repeat_tps", REPEAT_COLOR, "D"))
    numeric = all(re.fullmatch(r"\d+", name) for name in names) and len(names) > 1  # Nur Zahlen haben eine Reihenfolge, die eine Linie verdient.
    order = sorted(range(len(points)), key=lambda index: int(names[index])) if numeric else list(range(len(points)))
    place = {index: position for position, index in enumerate(order)}
    shown = [names[index] for index in order]
    failed = [place[index] for index, point in enumerate(points) if not point.get("tps") and not point.get("prompt_tps")]
    selected = str(step.get("selected"))
    chosen = next((place[index] for index, point in enumerate(points) if str(point.get("value")) == selected), None)
    comparison = step.get("key") == "final"
    axes = []
    for row, (label, key, color, marker) in enumerate(panels, start=1):
        axis = figure.add_subplot(len(panels), 1, row, sharex=axes[0] if axes else None)
        series = sorted((place[index], point[key]) for index, point in enumerate(points) if isinstance(point.get(key), (int, float)) and point[key] > 0)
        if numeric:
            _draw_panel(axis, label, series, color, marker, failed, compact)
            axis.set_ylim(0, axis.get_ylim()[1] * 1.15)  # Platz für die Zahlen über den Punkten.
            for position, value in series:
                axis.annotate(german_number(value), (position, value), xytext=(0, 7), textcoords="offset points", ha="center", va="bottom", fontsize=8 if compact else 10, color=TEXT_PRIMARY, zorder=5)
        else:
            _draw_bars(axis, series, color, marker, failed, compact, None if comparison else chosen)
        title = (_change_title(label, series) if comparison else None) or _param_title(label, series, shown)
        axis.set_title(title, loc="left", fontsize=9 if compact else 11, color=TEXT_PRIMARY, pad=4 if compact else 6)
        if row < len(panels):
            axis.tick_params(labelbottom=False)
        axes.append(axis)
    if not compact:
        title = f"{step.get('model', '')}   ·   {step.get('time', '')}   ·   {step.get('title', '')}"
        if step.get("quality"):
            title += "   ·   kann die Qualität beeinflussen"
        figure.suptitle(title, x=0.01, ha="left", fontsize=12, color=TEXT_PRIMARY)
    bottom = axes[-1]
    if not points:
        bottom.set_xticks([])
        return axes
    bottom.set_xlim(-0.5, len(points) - 0.5)
    bottom.set_xticks(range(len(points)))
    bottom.set_xticklabels([name + "\n(gewählt)" if position == chosen and not numeric and not comparison else name for position, name in enumerate(shown)])
    if step.get("flag"):
        bottom.set_xlabel(step["flag"], color=TEXT_SECONDARY, fontsize=8 if compact else 9)
    if failed:
        bottom.legend(loc="upper right", frameon=False, fontsize=8, labelcolor=TEXT_PRIMARY)
    if chosen is not None and numeric:
        for axis in axes:
            axis.axvline(chosen, color=GUIDE, linewidth=1, zorder=1)
        axes[0].text(chosen, 0.03, " gewählt", color=TEXT_SECONDARY, fontsize=8, va="bottom", ha="left", transform=axes[0].get_xaxis_transform())
    return axes


def save_param_png(step, path):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(11, 7), dpi=130, layout="constrained")
    figure.get_layout_engine().set(w_pad=0.15, h_pad=0.1)
    FigureCanvasAgg(figure)
    draw_param(figure, step)
    figure.savefig(path, facecolor=SURFACE)


def save_png(sweep, path):
    """Speichert den Plot unabhängig von der Fenstergröße in fester Auflösung."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(11, 7), dpi=130, layout="constrained")
    figure.get_layout_engine().set(w_pad=0.15, h_pad=0.1)
    FigureCanvasAgg(figure)
    draw_sweep(figure, sweep)
    figure.savefig(path, facecolor=SURFACE)
