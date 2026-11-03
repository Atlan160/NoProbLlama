"""Zeichnet den Kontext-Test: Prompt- und Antworttempo über der Kontextgröße, in zwei Plots mit geteilter X-Achse."""

import math

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
GUIDE = "#a8a7a2"
PROMPT_COLOR = "#2a78d6"
ANSWER_COLOR = "#eb6834"
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


def _draw_panel(axis, label, series, color, marker, failed, compact):
    axis.set_facecolor("none")
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(GRID)
    axis.tick_params(colors=TEXT_SECONDARY, length=0, labelsize=8 if compact else 9)
    axis.set_title(_panel_title(label, series), loc="left", fontsize=9 if compact else 11, color=TEXT_PRIMARY, pad=4 if compact else 6)
    if series:
        # Der Ring in Hintergrundfarbe hält die Marker auf der Linie lesbar.
        axis.plot(*zip(*series), color=color, marker=marker, linewidth=2, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2,
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


def draw_sweep(figure, sweep, compact=False):
    """Zeichnet einen Test in die Figure und liefert die Achsen für den Hover-Layer."""
    for axis in figure.axes:  # Beim Leeren setzt matplotlib die Grenzen zurück, das verträgt der Log-Maßstab nicht.
        axis.set_xscale("linear")
    figure.clear()
    figure.set_facecolor(SURFACE)
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


def save_png(sweep, path):
    """Speichert den Plot unabhängig von der Fenstergröße in fester Auflösung."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(11, 7), dpi=130, layout="constrained")
    figure.get_layout_engine().set(w_pad=0.15, h_pad=0.1)
    FigureCanvasAgg(figure)
    draw_sweep(figure, sweep)
    figure.savefig(path, facecolor=SURFACE)
