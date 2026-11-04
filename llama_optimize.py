"""Assistent „Modell optimieren“: misst Kontext und weitere Parameter der Reihe nach und speichert das Ergebnis als Profil."""

import json
import os
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import llama_plot

CONTEXT_TOLERANCE = 0.95  # So viel vom besten Antworttempo muss eine Kontextgröße halten, damit sie als verlustfrei gilt.
CONTEXT_PROMPT_TOLERANCE = 0.85
MOE_TOLERANCE = 0.98  # Innerhalb davon gewinnt der Wert mit weniger VRAM.
UB_TOLERANCE = 0.97
SPEC_NEW_TEXT_TOLERANCE = 0.95  # So viel darf Spekulation bei neuem Text kosten, wenn sie wiederholten Text beschleunigt.
TIE_TOLERANCE = 0.98
MOE_STEP = 2
MOE_MAX_POINTS = 9
UB_VALUES = ("256", "512", "1024", "2048")
KV_VALUES = ("f16", "q8_0", "q4_0")
SPEC_VALUES = ("none", "ngram-simple", "ngram-mod")
SPEC_MTP_VALUES = ("draft-mtp", "draft-mtp,ngram-mod")
MOE_FIELDS = ("cpu_moe", "n_cpu_moe", "moe_cache")
PANEL_BACKGROUND = "#fff4d6"
LIST_BACKGROUND = "#ffffff"
LIST_SELECTED = "#d9d8d3"
SIDE_WIDTH = 280
WINDOW_WIDTH = 1540
WINDOW_HEIGHT = 860
RANGE_POINTS = 7
RANGE_MIN_STEP = 1.05
MIN_CONTEXT = 1024
FULL_PROMPT_CONTEXT = 8448  # Ab hier passt der ganze Einlese-Prompt. Darunter wird er kürzer und das gemessene Tempo weniger vergleichbar.
CONTEXT_MIN_GAP = 0.1  # Kontexte, die so dicht am gewählten liegen, zeigen nichts Neues und überdecken sich im Diagramm.

TEXTS = {
    "context": ("Kontextgröße", "-c", "Misst das Tempo bei verschiedenen Kontextgrößen. Wähle danach die Größe, die du nutzen möchtest."),
    "range": ("Kontextgröße", "-c", "Wähle den Bereich, in dem gemessen werden soll. Dazwischen verteilt das Programm 7 Messpunkte."),
    "ncmoe": ("MoE-Experten", "-ncmoe", "Sucht, wie viele Expertenschichten in den RAM sollten. Der beste Wert wird automatisch gewählt."),
    "ub": ("Micro-Batch", "-ub", "Sucht das schnellste Einlesen, ohne dass das Antworttempo leidet. Der beste Wert wird automatisch gewählt."),
    "spec": ("Spekulation", "--spec-type", "Misst neuen und wiederholten Text. Gewählt wird das Schnellste bei wiederholtem Text, das neuen Text höchstens 5 % kostet."),
    "gate": ("Qualität", "", ""),
    "ctk": ("KV-Cache K", "-ctk", "Kann die Qualität beeinflussen. Wähle den Typ, den du nutzen möchtest."),
    "ctv": ("KV-Cache V", "-ctv", "Kann die Qualität beeinflussen. Wähle den Typ, den du nutzen möchtest."),
    "c": ("Kontext erneut", "-c", "Kann die Qualität beeinflussen, weil das Modell weniger sieht, wenn der Kontext kleiner wird. Wähle die Größe, die du nutzen möchtest."),
    "final": ("Ergebnis", "", "Misst die gewählte Kombination und vergleicht sie mit dem Ausgangspunkt."),
}
MEANINGS = {
    "context": "Wie viele Tokens das Modell gleichzeitig sehen kann: Verlauf, Dateien und die aktuelle Antwort.",
    "ncmoe": "Bei wie vielen Schichten die Experten im RAM liegen. Die übrigen rechnen schneller im VRAM.",
    "ub": "Wie viele Tokens auf einmal berechnet werden. Mehr liest lange Prompts schneller ein, braucht aber VRAM.",
    "spec": "Sagt die nächsten Tokens voraus und lässt das Modell sie gemeinsam prüfen. Das beschleunigt wiederholten Text.",
    "gate": "Freiwillig: Die folgenden Parameter können die Antworten verändern.",
    "ctk": "Datentyp der K-Werte im Kontextspeicher. Kleinere Typen sparen VRAM, sind aber ungenauer.",
    "ctv": "Datentyp der V-Werte im Kontextspeicher. Sie wirken stärker auf das Ergebnis als die K-Werte.",
    "c": "Kontextgröße mit den gewählten Typen: Kleinere Typen schaffen oft Platz für mehr Tokens.",
    "final": "Misst die gewählte Kombination und vergleicht sie mit dem Ausgangspunkt.",
}
QUALITY_STEPS = ("ctk", "ctv", "c")
SWEEP_STEPS = ("context", "c")
CHOICE_STEPS = ("context", "ctk", "ctv", "c")


def ok_points(points):
    return [point for point in points if point.get("state") == "OK" and point.get("tps")]


def recommend_context(points):
    """Der größte Kontext, bei dem Antwort und Einlesen noch etwa so schnell sind wie am besten Punkt."""
    measured = ok_points(points)
    if not measured:
        return None
    best = max(point["tps"] for point in measured)
    best_prompt = max((point.get("prompt_tps") or 0 for point in measured), default=0)
    fine = [point for point in measured if point["tps"] >= best * CONTEXT_TOLERANCE
            and (not best_prompt or (point.get("prompt_tps") or 0) >= best_prompt * CONTEXT_PROMPT_TOLERANCE)]
    return max(fine or measured, key=lambda point: point["ctx"])


def best_point(key, points):
    """Wendet die Regel des jeweiligen Schritts an. Liefert den besten gemessenen Punkt oder None."""
    measured = ok_points(points)
    if not measured:
        return None
    top = max(point["tps"] for point in measured)
    if key in SWEEP_STEPS:
        return recommend_context(points)
    if key == "ncmoe":
        fine = [point for point in measured if point["tps"] >= top * MOE_TOLERANCE]
        return max(fine, key=lambda point: int(point["value"]))
    if key == "ub":
        fine = [point for point in measured if point["tps"] >= top * UB_TOLERANCE]
        top_prompt = max(point.get("prompt_tps") or 0 for point in fine)
        fast = [point for point in fine if (point.get("prompt_tps") or 0) >= top_prompt * UB_TOLERANCE]
        return min(fast, key=lambda point: int(point["value"]))
    if key == "spec":
        fine = [point for point in measured if point["tps"] >= top * SPEC_NEW_TEXT_TOLERANCE]
        return max(fine, key=lambda point: point.get("repeat_tps") or point["tps"])
    # Die Liste beginnt beim genauesten Typ. Er bleibt, solange ein kleinerer kaum schneller ist.
    return next(point for point in measured if point["tps"] >= top * TIE_TOLERANCE)


def moe_plan(center, layers):
    values = (center + offset for offset in (-2 * MOE_STEP, -MOE_STEP, 0, MOE_STEP, 2 * MOE_STEP))
    return sorted({min(layers, max(0, value)) for value in values})


def moe_more(points, layers):
    """Liegt das Beste am Rand der Messreihe, geht die Suche in dieser Richtung weiter."""
    measured = ok_points(points)
    tried = sorted(int(point["value"]) for point in points)
    if not measured or len(tried) >= MOE_MAX_POINTS:
        return []
    best = int(max(measured, key=lambda point: point["tps"])["value"])
    if best == tried[0] and best > 0:
        return [max(0, best - MOE_STEP)]
    if best == tried[-1] and best < layers:
        return [min(layers, best + MOE_STEP)]
    return []


def parse_tokens(text):
    """Liest eine Kontextgröße wie 32768, 32.768 oder 32k. Wirft ValueError, wenn das nicht geht."""
    match = re.fullmatch(r"(\d[\d.]*)\s*([kK]?)", text.strip())
    if not match:
        raise ValueError(f"„{text.strip()}“ ist keine Kontextgröße. Beispiele: 16k, 32768, 260k.")
    return int(match.group(1).replace(".", "")) * (1024 if match.group(2) else 1)


def read_range(low_text, high_text, trained):
    """Prüft den Kontext-Bereich gegen das Modell. Liefert (von, bis, Hinweis). Liegt „bis“ darüber, gilt das Maximum des Modells."""
    low, high = parse_tokens(low_text), parse_tokens(high_text)
    if low < MIN_CONTEXT:
        raise ValueError(f"Der Kontext muss mindestens {llama_plot.german_number(MIN_CONTEXT, 0)} Tokens groß sein.")
    if trained and low >= trained:
        raise ValueError(f"„Von“ muss unter dem Maximum dieses Modells von {llama_plot.german_number(trained, 0)} Tokens liegen.")
    note = ""
    if trained and high > trained:
        high = trained
        note = f"Das Modell unterstützt höchstens {llama_plot.german_number(trained, 0)} Tokens, deshalb wird nur bis dahin gemessen."
    if high <= low:
        raise ValueError("„Bis“ muss größer sein als „Von“.")
    return low, high, note


def range_contexts(low, high, limit=None):
    """Verteilt die Messpunkte gleichmäßig auf der logarithmischen Achse. Die geschätzte VRAM-Grenze wird einer davon, denn dort knickt das Tempo meist ab."""
    ratio = (high / low) ** (1 / (RANGE_POINTS - 1))
    inner = [round(low * ratio ** number / 1024) * 1024 for number in range(1, RANGE_POINTS - 1)]
    inner = [value for value in inner if low < value < high]
    if limit and low < limit < high:
        if inner:
            inner.remove(min(inner, key=lambda value: abs(value - limit)))
        inner.append(limit)
    points = [low]
    for value in sorted(inner) + [high]:  # Sehr dicht beieinanderliegende Punkte zeigen nichts Neues und überdecken sich im Diagramm.
        if value >= points[-1] * RANGE_MIN_STEP or value == high:
            points.append(value)
    if len(points) > 2 and points[-1] < points[-2] * RANGE_MIN_STEP:
        del points[-2]
    return points


def with_current(values, current):
    """Der aktuelle Wert gehört immer zur Messreihe, damit der Ausgangspunkt vergleichbar bleibt."""
    return list(values) + ([current] if current and current not in values else [])


def repeat_lines(target):
    """Immer derselbe Code. Abschreiben zeigt, was Spekulation bei wiederholtem Text bringt."""
    return "\n".join(f"def funktion_{number}(wert):\n    return wert * {number} + {number % 7}\n" for number in range(max(target, 400) // 22))


class OptimizeWindow(tk.Toplevel):
    def __init__(self, app, exe, model, info, values, free, folder):
        super().__init__(app)
        self.app = app
        self.exe = exe
        self.model = model
        self.info = info
        self.start_values = dict(values)
        self.current = dict(values)
        self.free = free
        self.folder = Path(folder)
        self.stamp = time.strftime("%d.%m.%Y %H:%M")
        self.cancel = None
        self.running = False
        self.steps = []
        self.step = None
        self.shown = None
        self.base_point = None
        self.saved_profile = ""
        self.closed = False
        self.figure = None
        self.canvas = None
        self.measure_total = 0
        self.aborted = False
        self.final_changed = False
        self.final_result = None
        self.final_backup = None
        self.range = None
        self.range_default = app.optimize_range_default(self.current)
        self.title(f"Modell optimieren: {model.stem}")
        self.geometry(f"{min(WINDOW_WIDTH, self.winfo_screenwidth() - 40)}x{min(WINDOW_HEIGHT, self.winfo_screenheight() - 80)}")  # So breit, dass die Balkenbeschriftungen lesbar sind.
        self.minsize(1000, 780)  # Darunter passen nicht mehr alle Schritte samt Erklärung in die linke Liste.
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        for key in ("context", "ncmoe", "ub", "spec", "gate", "final"):
            if key != "ncmoe" or info.get("experts"):  # Normale Modelle haben keine Experten, die sich verteilen ließen.
                self.steps.append(self._new_step(key))
        self._refresh_list()
        self._begin(self.steps[0])

    # ---------- Aufbau ----------

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        side = ttk.Frame(self, padding=(10, 10, 6, 10))
        side.grid(row=0, column=0, sticky="ns")
        ttk.Label(side, text="Schritte", style="Head.TLabel").pack(anchor="w")
        self.step_list = tk.Frame(side, background=LIST_BACKGROUND, width=SIDE_WIDTH)
        self.step_list.pack(fill="y", expand=True, pady=(4, 0))
        self.step_list.pack_propagate(False)

        main = ttk.Frame(self, padding=(4, 10, 10, 10))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        main.bind("<Configure>", lambda event: self._fit_text(event.width), add="+")
        self.head_var = tk.StringVar()
        ttk.Label(main, textvariable=self.head_var, font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        self.explain_var = tk.StringVar()
        self.explain = ttk.Label(main, textvariable=self.explain_var, wraplength=650, justify="left")
        self.explain.grid(row=1, column=0, sticky="w", pady=(2, 6))

        self.body = ttk.Frame(main)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self._build_range_page()
        self._build_measure_page()
        self._build_gate_page()
        self._build_final_page()

        status = ttk.Frame(main)
        status.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.bar = ttk.Progressbar(status, length=170)
        self.bar.pack(side="left")
        self.status_var = tk.StringVar()
        ttk.Label(status, textvariable=self.status_var).pack(side="left", padx=(10, 0))

        nav = ttk.Frame(main)
        nav.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.abort_button = ttk.Button(nav, text="Abbrechen", style="Big.TButton", command=self.abort)
        self.abort_button.pack(side="left")
        ttk.Button(nav, text="Diagramme öffnen", style="Big.TButton", command=self.open_folder).pack(side="left", padx=(6, 0))
        self.next_button = ttk.Button(nav, text="Weiter", style="Big.TButton", command=self.next_step, state="disabled")
        self.next_button.pack(side="right")

    def _fit_text(self, width):
        """Die langen Texte nutzen die Breite, die das Fenster gerade hat."""
        wrap = max(400, width - 30)
        self.explain.configure(wraplength=wrap)
        self.final_note_label.configure(wraplength=wrap)
        self.gate_warning.configure(wraplength=wrap - 30)
        self.range_note.configure(wraplength=wrap)

    def _build_range_page(self):
        self.range_page = page = ttk.Frame(self.body)
        low, high, _limit, trained = self.range_default
        maximum = f"Dieses Modell unterstützt bis zu {llama_plot.german_number(trained, 0)} Tokens Kontext." if trained else "Die Modelldatei nennt keine Obergrenze für den Kontext."
        ttk.Label(page, text=maximum, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(page)
        row.pack(anchor="w", pady=(10, 0))
        self.range_low_var = tk.StringVar(value=str(low))
        self.range_high_var = tk.StringVar(value=str(high))
        for label, var in (("Von", self.range_low_var), ("Bis", self.range_high_var)):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 6))
            ttk.Entry(row, textvariable=var, width=12, font=("Segoe UI", 11)).pack(side="left", padx=(0, 16))
            var.trace_add("write", lambda *_: self._update_range())
        ttk.Label(row, text="zum Beispiel 16k, 32768 oder 260k", style="Hint.TLabel").pack(side="left")
        self.range_points_var = tk.StringVar()
        ttk.Label(page, textvariable=self.range_points_var, justify="left").pack(anchor="w", pady=(12, 0))
        self.range_note_var = tk.StringVar()
        self.range_note = ttk.Label(page, textvariable=self.range_note_var, justify="left", wraplength=800)
        self.range_note.pack(anchor="w", pady=(6, 0))
        self.start_button = ttk.Button(page, text="Messung starten", style="Big.TButton", command=self.start_range)
        self.start_button.pack(anchor="w", pady=(14, 0))
        self._update_range()

    def _update_range(self):
        """Zeigt beim Tippen, was gemessen würde und ob das Modell den Bereich unterstützt."""
        _low, _high, limit, trained = self.range_default
        try:
            low, high, note = read_range(self.range_low_var.get(), self.range_high_var.get(), trained)
        except ValueError as err:
            self.range_points_var.set("")
            self.range_note_var.set(str(err))
            self.range_note.configure(foreground="#b3261e")
            self.start_button.configure(state="disabled")
            return None
        points = range_contexts(low, high, limit)
        self.range_points_var.set("Messpunkte: " + ", ".join(llama_plot.compact_tokens(value) for value in points))
        notes = [note] if note else []
        if limit and low < limit < high:
            notes.append(f"Laut Schätzung passt alles bis etwa {llama_plot.compact_tokens(limit)} in den VRAM. Dieser Wert ist als Messpunkt dabei.")
        elif limit and limit >= high:
            notes.append("Laut Schätzung passt der ganze Bereich in den VRAM.")
        elif limit is not None:
            notes.append("Laut Schätzung passt schon der kleinste Wert nicht ganz in den VRAM.")
        if low < FULL_PROMPT_CONTEXT:
            notes.append(f"Unter {llama_plot.german_number(FULL_PROMPT_CONTEXT, 0)} Tokens wird der Prompt beim Einlesen kürzer. Diese Werte sind nicht ganz vergleichbar.")
        self.range_note_var.set("\n".join(notes))
        self.range_note.configure(foreground="#b35900" if note else "")
        self.start_button.configure(state="normal")
        return low, high

    def start_range(self):
        chosen = self._update_range()
        if chosen:
            self.range = chosen
            self._begin(self.steps[0])

    def _build_measure_page(self):
        self.measure_page = page = ttk.Frame(self.body)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.plot = ttk.Frame(page)
        self.plot.grid(row=0, column=0, sticky="nsew")
        columns = {"value": ("Wert", 160), "prompt_tps": ("Prompt Tok/s", 84), "tps": ("Tok/s", 52), "repeat_tps": ("Wiederholt", 72), "vram": ("VRAM MiB", 66), "state": ("Status", 54)}
        table = ttk.Frame(page)
        table.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        table.rowconfigure(1, weight=1)
        self.pick_var = tk.StringVar()
        self.table = ttk.Treeview(table, columns=list(columns), show="headings", selectmode="browse", height=14)
        for key, (title, width) in columns.items():
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="center", stretch=False)
        self.table.grid(row=1, column=0, sticky="ns")
        self.table.bind("<<TreeviewSelect>>", lambda _: self._on_table_pick())
        self.table.tag_configure("best", font=("Segoe UI", 9, "bold"))
        ttk.Label(table, textvariable=self.pick_var, wraplength=440, justify="left").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.redo_button = ttk.Button(table, text="Ab hier neu messen", style="Big.TButton", command=self.redo_from_shown)
        self.redo_button.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.redo_button.grid_remove()
        self._ensure_canvas()

    def _build_gate_page(self):
        self.gate_page = page = ttk.Frame(self.body)
        self.gate_warning = warning = tk.Label(page, background=PANEL_BACKGROUND, justify="left", anchor="w", wraplength=640, padx=12, pady=10, font=("Segoe UI", 10),
                                               text="Achtung: Die nächsten Parameter können die Qualität der Antworten beeinflussen. Kleinere KV-Cache-Typen sparen VRAM, speichern "
                                "den Kontext aber ungenauer. Ein kleinerer Kontext bedeutet, dass das Modell weniger vom Gespräch sieht. Die Messung zeigt nur das "
                                "Tempo, nicht die Qualität. Prüfe das Ergebnis deshalb mit deinen eigenen Aufgaben.")
        warning.pack(fill="x")
        ttk.Label(page, text="Was soll gemessen werden?", style="Head.TLabel").pack(anchor="w", pady=(12, 4))
        self.gate_vars = {}
        for key, text in (("ctk", "-ctk: KV-Cache-Typ K"), ("ctv", "-ctv: KV-Cache-Typ V"), ("c", "-c: Kontextgröße mit den gewählten KV-Typen")):
            self.gate_vars[key] = tk.BooleanVar(value=True)
            ttk.Checkbutton(page, text=text, variable=self.gate_vars[key]).pack(anchor="w", pady=2)
        row = ttk.Frame(page)
        row.pack(anchor="w", pady=(12, 0))
        ttk.Button(row, text="Ausgewählte messen", style="Big.TButton", command=self._gate_measure).pack(side="left")
        ttk.Button(row, text="Überspringen", style="Big.TButton", command=self._gate_skip).pack(side="left", padx=(6, 0))

    def _build_final_page(self):
        self.final_page = page = ttk.Frame(self.body)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        top = ttk.Frame(page)
        top.grid(row=0, column=0, sticky="nsew")
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        self.final_plot = ttk.Frame(top)
        self.final_plot.grid(row=0, column=0, sticky="nsew")
        self.final_table = ttk.Frame(top)
        self.final_table.grid(row=0, column=1, sticky="n", padx=(8, 0))
        self.final_note = tk.StringVar()
        self.final_note_label = ttk.Label(page, textvariable=self.final_note, wraplength=650, justify="left")
        self.final_note_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.command_text = tk.Text(page, height=3, wrap="word", font=("Consolas", 9), state="disabled", background="#f4f4f4", relief="flat", padx=6, pady=4)
        self.command_text.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        row = ttk.Frame(page)
        row.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(row, text="Profilname").pack(side="left", padx=(0, 6))
        self.profile_var = tk.StringVar(value=self.model.stem)
        ttk.Entry(row, textvariable=self.profile_var, width=46).pack(side="left")
        self.save_button = ttk.Button(row, text="Als Profil speichern", style="Big.TButton", command=self.save_profile)
        self.save_button.pack(side="left", padx=(8, 0))
        self.remeasure_button = ttk.Button(row, text="Ergebnis neu messen", style="Big.TButton", command=self.remeasure_final)
        self.remeasure_button.pack(side="left", padx=(8, 0))
        self.final_canvas = None

    def _ensure_canvas(self):
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            ttk.Label(self.plot, justify="left", text="Für die Diagramme fehlt matplotlib: py -m pip install matplotlib\nDie Messwerte stehen trotzdem in der Tabelle.").pack(anchor="nw")
            return
        self.figure = Figure(layout="constrained", dpi=96)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.plot)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # ---------- Schritte ----------

    def _new_step(self, key):
        title, flag, _ = TEXTS[key]
        return {"key": key, "title": title, "flag": flag, "quality": key in QUALITY_STEPS, "model": self.model.stem, "time": self.stamp, "points": [],
                "selected": None, "done": False, "skipped": False, "image": str(self.folder / f"{len(self.steps) + 1:02d}_{key}.png")}

    def _refresh_list(self):
        for child in self.step_list.winfo_children():
            child.destroy()
        for step in self.steps:
            mark = "✓" if step["done"] and not step["skipped"] else "–" if step["skipped"] else "▸" if step is self.step else " "
            color = LIST_SELECTED if step is self.shown else LIST_BACKGROUND
            row = tk.Frame(self.step_list, background=color, padx=6, pady=3)
            row.pack(fill="x")
            title = tk.Label(row, text=f"{mark}  {step['title']}", background=color, anchor="w", font=("Segoe UI", 10, "bold"), foreground="#b35900" if step["quality"] else "#1a1a1a")
            title.pack(fill="x")
            meaning = tk.Label(row, text=MEANINGS[step["key"]], background=color, anchor="w", justify="left", wraplength=SIDE_WIDTH - 20, font=("Segoe UI", 8), foreground="#52514e")
            meaning.pack(fill="x")
            if step["points"] or step is self.step:  # Der aktuelle Schritt bleibt immer erreichbar, auch ohne Messwerte.
                for widget in (row, title, meaning):
                    widget.configure(cursor="hand2")
                    widget.bind("<Button-1>", lambda _event, target=step: self._open_step(target))

    def _open_step(self, step):
        """Wer nach einer Änderung zum Ergebnis zurückkehrt, bekommt es gleich neu gemessen."""
        if step is self.steps[-1] and step["done"] and self.final_changed and self.shown is not step and not self.running:
            self.remeasure_final()
        else:
            self._show(step)

    def _show_page(self, page):
        for candidate in (self.range_page, self.measure_page, self.gate_page, self.final_page):
            if candidate is page:
                candidate.grid(row=0, column=0, sticky="nsew")
            else:
                candidate.grid_remove()

    def _show(self, step):
        self.shown = step
        self._refresh_list()
        title, flag, text = TEXTS[step["key"]]
        self.head_var.set(f"{title}" + (f"   ({flag})" if flag else ""))
        self.explain_var.set(text)
        self.explain.configure(foreground="#b35900" if step["quality"] else "")
        self._update_nav()
        if self.range is None:
            self.explain_var.set(TEXTS["range"][2])
            self._show_page(self.range_page)
            return
        if step["key"] == "gate":
            self.head_var.set("Parameter, die die Qualität beeinflussen können")
            self.explain_var.set("Dieser Teil ist freiwillig.")
            self._show_page(self.gate_page)
            return
        if step["key"] == "final" and step["done"]:
            self.explain_var.set("Die Auswahl lässt sich hier noch ändern. Danach speicherst du sie als Profil mit dem Namen des Modells.")
            self._show_page(self.final_page)
            return
        self._show_page(self.measure_page)
        self._fill_table(step)
        self._draw(step)

    def _update_nav(self):
        """Der Knopf rechts führt weiter oder, beim Blick auf einen anderen Schritt, zurück zum aktuellen."""
        step, button = self.step, self.next_button
        if step is None:
            return
        if self._can_redo():
            self.redo_button.grid()
        else:
            self.redo_button.grid_remove()
        if self.shown is not step:
            button.pack(side="right")
            button.configure(text="Zurück zum aktuellen Schritt", state="normal")
            return
        if step is self.steps[-1] and step["done"]:
            button.pack_forget()
            return
        button.pack(side="right")
        waiting = step["key"] in CHOICE_STEPS and step["done"] and not self.running and not self.aborted
        best = best_point(step["key"], step["points"]) if waiting else None
        if best:
            button.configure(text="Auswahl übernehmen und weiter", state="normal")
        elif waiting and step["key"] != "context":
            button.configure(text="Weiter ohne Änderung", state="normal")
        else:
            button.configure(text="Weiter", state="disabled")

    def _fill_table(self, step):
        self.table.delete(*self.table.get_children())
        best = best_point(step["key"], step["points"]) if step["key"] != "final" else None
        for index, point in enumerate(step["points"]):
            tags = ("best",) if point is best else ()
            star = "★ " if point is best else ""
            self.table.insert("", "end", iid=str(index), tags=tags, values=(
                star + str(point.get("label", point.get("value", ""))), point.get("prompt_tps", ""), point.get("tps", ""), point.get("repeat_tps", ""),
                point.get("vram") or "", point.get("state", "")))
        selected = next((index for index, point in enumerate(step["points"]) if str(point.get("value")) == str(step["selected"])), None)
        if selected is not None:
            self.table.selection_set(str(selected))
        else:
            self.table.selection_remove(*self.table.selection())
        self.pick_var.set(self._pick_text(step, best))

    def _pick_text(self, step, best):
        if not step["points"]:
            return ""
        lines = []
        if best and step["key"] != "final":
            lines.append(f"★ Empfehlung: {best.get('label', best.get('value'))}")
        if step["selected"] is not None:
            lines.append(f"Gewählt: {self._label(step, step['selected'])}")
        if step["key"] not in ("final", "gate") and step["done"] and not self.running:
            lines.append("Zum Ändern eine Zeile anklicken.")
            later = self.steps[self.steps.index(step) + 1:]
            if any(other["points"] for other in later):
                lines.append("Die späteren Schritte stammen noch vom vorherigen Wert. „Ab hier neu messen“ wiederholt sie.")
        return "\n".join(lines)

    @staticmethod
    def _label(step, value):
        return next((point.get("label", point.get("value")) for point in step["points"] if str(point.get("value")) == str(value)), value)

    def _draw(self, step):
        if not self.canvas:
            return
        self._draw_into(self.figure, step)
        self.canvas.draw_idle()

    @staticmethod
    def _draw_into(figure, step, compact=True):
        if step["key"] in SWEEP_STEPS:
            llama_plot.draw_sweep(figure, step["sweep"], compact=compact)
        else:
            llama_plot.draw_param(figure, step, compact=compact)

    def _settings(self, step, value):
        """Liefert (Einstellungen, Kontext, wiederholten Text messen) für einen Messpunkt."""
        settings = dict(self.current)
        key = step["key"]
        context = int(value) if key in SWEEP_STEPS else int(self.current["ctx_size"])
        if key == "context":
            settings.update(self._moe_settings(settings, context))
        elif key == "ncmoe":
            settings.update(ngl="all", cpu_moe="", n_cpu_moe=str(value))
        elif key == "ub":
            settings["ubatch"] = str(value)
            batch = settings.get("batch", "").strip()
            if batch.isdigit() and int(batch) < int(value):
                settings["batch"] = str(value)
        elif key == "spec":
            settings["spec_type"] = str(value)
        elif key == "ctk":
            settings["cache_k"] = str(value)
        elif key == "ctv":
            settings["cache_v"] = str(value)
        elif key == "final" and value == "Ausgang":
            settings = dict(self.start_values)  # Der Ausgangspunkt mit denselben Einstellungen wie im Kontext-Schritt, nur bei der gewählten Größe.
            settings.update(self._moe_settings(settings, context))
        return settings, context, key == "spec"

    def _final_base(self, context):
        """Ausgangswert bei dieser Kontextgröße, falls er schon gemessen ist. Sonst muss er mitgemessen werden, damit der Vergleich fair bleibt."""
        known = self.steps[-1].setdefault("baselines", {}).get(str(context))
        if known:
            return dict(known)
        return next((dict(point) for point in ok_points(self.steps[0]["points"]) if point["value"] == str(context)), None)

    def _moe_settings(self, settings, context):
        """Ohne eigene MoE-Felder bekommt jede Kontextgröße die passende Verteilung, sonst würde --fit das Modell ausbremsen."""
        if not self.info.get("expert_layers") or any(settings.get(key, "").strip() for key in MOE_FIELDS):
            return {}
        suggestion = self.app.optimize_moe_suggestion(settings, context, self.free)
        return {"ngl": "all", "n_cpu_moe": str(suggestion["n_cpu_moe"])} if suggestion else {}

    def _plan(self, step):
        key = step["key"]
        if key == "context":
            return range_contexts(*self.range, self.app.optimize_limit(self.current))
        if key == "ncmoe":
            current = self.current.get("n_cpu_moe", "").strip()
            layers = self.info["expert_layers"]
            suggestion = self.app.optimize_moe_suggestion(self.current, int(self.current["ctx_size"]), self.free)
            center = int(current) if current.isdigit() else suggestion["n_cpu_moe"] if suggestion else layers // 2
            return moe_plan(center, layers)
        if key == "ub":
            return with_current(UB_VALUES, self.current.get("ubatch", "").strip() or "512")
        if key == "spec":
            values = list(SPEC_VALUES) + (list(SPEC_MTP_VALUES) if self.info.get("mtp_layers") else [])
            return with_current(values, self.current.get("spec_type", "").strip() or "none")
        if key == "ctk":
            return with_current(KV_VALUES, self.current.get("cache_k", "").strip() or "f16")
        if key == "ctv":
            return with_current(KV_VALUES, self.current.get("cache_v", "").strip() or "f16")
        if key == "c":
            chosen = int(self.current["ctx_size"])
            planned = range_contexts(*self.range, self.app.optimize_limit(self.current))  # Kleinere KV-Typen verschieben die VRAM-Grenze.
            return sorted({value for value in planned if value >= chosen // 2 and abs(value - chosen) > chosen * CONTEXT_MIN_GAP} | {chosen})
        return ["Ergebnis"] if self._final_base(int(self.current["ctx_size"])) else ["Ausgang", "Ergebnis"]

    def _start_value(self, key):
        return {"ncmoe": "n_cpu_moe", "ub": "ubatch", "spec": "spec_type", "ctk": "cache_k", "ctv": "cache_v"}.get(key)

    def _begin(self, step):
        self.step = step
        if self.range is None:
            self.status_var.set("Wähle den Bereich und starte die Messung.")
            self._show(step)
            return
        if step["key"] == "gate":
            self.status_var.set("Wähle, was gemessen werden soll, oder überspringe den Schritt.")
            self._show(step)
            return
        if step["key"] in SWEEP_STEPS:
            step["sweep"] = {"time": self.stamp, "model": self.model.stem, "settings": self.app.optimize_describe(self.current), "fit_limit": self.app.optimize_limit(self.current),
                             "free": self.free, "contexts": [], "points": step["points"], "complete": False, "image": step["image"]}
        self._measure(step, self._plan(step))

    def _measure(self, step, values):
        jobs = []
        for value in values:
            settings, context, repeat = self._settings(step, value)
            jobs.append((value, settings, context, repeat))
        if step["key"] in SWEEP_STEPS:
            step["sweep"]["contexts"] = sorted(set(step["sweep"]["contexts"]) | {job[0] for job in jobs})
        self.running = True
        self.cancel = threading.Event()
        done = len(step["points"])
        self.measure_total = done + len(jobs)
        self.bar.configure(value=done, maximum=max(self.measure_total, 1))
        self.abort_button.configure(text="Abbrechen")
        self._show(step)
        threading.Thread(target=self._worker, args=(step, jobs, done, self.cancel), daemon=True).start()

    def _worker(self, step, jobs, done, cancel):
        for index, (value, settings, context, repeat) in enumerate(jobs):
            if cancel.is_set():
                break
            started = time.perf_counter()
            position = done + index + 1

            def announce(phase, shown=value, at=position):
                self.app.events.put(lambda: self._on_phase(step, shown, at, phase))

            point = self.app._measure_context(self.exe, self.model, settings, context, cancel, announce, repeat)
            if cancel.is_set():
                break
            point["value"] = str(value)
            point["label"] = llama_plot.compact_tokens(int(value)) if step["key"] in SWEEP_STEPS else str(value)
            point["settings"] = {key: settings.get(key, "") for key in ("ngl", "n_cpu_moe", "ubatch", "batch", "spec_type", "cache_k", "cache_v")}
            point["seconds"] = round(time.perf_counter() - started, 1)
            self.app.events.put(lambda p=point, at=position: self._on_point(step, p, at))
        self.app.events.put(lambda: self._on_measured(step, cancel.is_set()))

    def _alive(self):
        return not self.closed and self.winfo_exists()

    def _on_phase(self, step, value, at, phase):
        if not self._alive() or not self.running or self.cancel.is_set():
            return
        finished = [point["seconds"] for point in step["points"] if point.get("seconds")]
        remaining = ""
        if finished:
            minutes = sum(finished) / len(finished) * (self.measure_total - at + 1) / 60
            remaining = f"   ·   noch etwa {max(1, round(minutes))} Min. in diesem Schritt"
        shown = llama_plot.compact_tokens(int(value)) if step["key"] in SWEEP_STEPS else value
        self.status_var.set(f"{TEXTS[step['key']][0]}, Punkt {at} von {self.measure_total}: {shown}, {phase}{remaining}")

    def _on_point(self, step, point, at):
        if not self._alive():
            return
        step["points"].append(point)
        self.bar.configure(value=at)
        if point.get("error"):
            self.app._log_app(f"Optimieren, {step['flag']} {point['value']}: {point['error']}")
        self._persist(step)
        if self.shown is step:
            self._fill_table(step)
            self._draw(step)

    def _persist(self, step):
        """Jeder Punkt wird sofort gesichert: Messwerte, Diagramm und bei Kontext-Schritten auch der Eintrag im Kontext-Test."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if step["key"] in SWEEP_STEPS:
            step["sweep"]["complete"] = len(step["points"]) >= len(step["sweep"]["contexts"])
            self.app._store_sweep(step["sweep"])
        else:
            self._save_image(step)
        self._save_report()

    def _save_image(self, step):
        try:
            llama_plot.save_param_png(step, step["image"])
        except ImportError:
            self.app._log_app("Für das Diagramm fehlt matplotlib: py -m pip install matplotlib.")
        except (OSError, ValueError) as err:
            self.app._log_app(f"Diagramm nicht gespeichert: {err}")

    def _save_report(self):
        report = {"model": self.model.stem, "time": self.stamp, "start": self.start_values, "current": self.current, "profile": self.saved_profile,
                  "steps": [{key: value for key, value in step.items() if key != "sweep"} for step in self.steps if step["points"] or step["skipped"]]}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "ergebnis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as err:
            self.app._log_app(f"Ergebnis nicht gespeichert: {err}")

    def _on_measured(self, step, cancelled):
        if self.closed:  # Erst jetzt ist der Testserver weg und sein VRAM frei.
            self.app.on_optimizer_closed()
            return
        self.running = False
        if cancelled and step["key"] == "final" and self.final_backup:
            # Das letzte fertige Ergebnis bleibt, es passt nur nicht mehr zur Auswahl.
            step.update(self.final_backup, done=True)
            self.final_backup = None
            self.final_changed = True
            self.abort_button.configure(text="Schließen")
            self._update_final_note()
            self._show(step)
            self.status_var.set("Neue Messung abgebrochen. Das Diagramm zeigt noch das vorherige Ergebnis.")
            return
        if cancelled:
            self.status_var.set("Abgebrochen. Die fertigen Messpunkte und Diagramme bleiben gespeichert.")
            self.abort_button.configure(text="Schließen")
            self.aborted = True
            self._update_nav()
            return
        key = step["key"]
        if key == "ncmoe":
            more = moe_more(step["points"], self.info["expert_layers"])
            if more:  # Das Beste lag am Rand, die Suche geht in dieser Richtung weiter.
                self._measure(step, more)
                return
        if key == "final":
            self._finish_final(step)
            return
        best = best_point(key, step["points"])
        step["done"] = True
        if key in CHOICE_STEPS:
            self._await_choice(step, best)
            return
        if best is None:
            self.status_var.set(f"{TEXTS[key][0]}: Kein Messpunkt war erfolgreich, der bisherige Wert bleibt.")
            self.app._log_app(f"Optimieren: {step['flag']} ohne Ergebnis, der bisherige Wert bleibt.")
        else:
            step["selected"] = best["value"]
            self.current.update({name: value for name, value in best["settings"].items() if name in self._fields(key)})
            self.app._log_app(f"Optimieren: {step['flag']} {best['value']} gewählt.")
        self._persist(step)
        self._fill_table(step)
        self._go_on(step)

    @staticmethod
    def _fields(key):
        return {"ncmoe": ("ngl", "n_cpu_moe"), "ub": ("ubatch", "batch"), "spec": ("spec_type",), "ctk": ("cache_k",), "ctv": ("cache_v",)}.get(key, ())

    def _await_choice(self, step, best):
        """Bei den Kontextgrößen ist die Empfehlung vorgewählt. Bei KV-Typen bleibt der bisherige Wert, weil sie die Qualität berühren."""
        key = step["key"]
        keep = self.current.get(self._start_value(key) or "", "").strip() or "f16" if key in ("ctk", "ctv") else self.current.get("ctx_size", "") if key == "c" else None
        measured = {point["value"] for point in ok_points(step["points"])}
        if keep in measured:
            step["selected"] = keep
        else:
            step["selected"] = best["value"] if best else None
        self._fill_table(step)
        self._update_nav()
        if best is None:
            self.status_var.set("Kein Messpunkt war erfolgreich. Prüfe das Server-Log und die Einstellungen.")
            if key == "context":
                self.abort_button.configure(text="Schließen")
            return
        self.status_var.set("Fertig gemessen. Wähle in der Tabelle den gewünschten Wert und gehe weiter.")

    def _on_table_pick(self):
        """Eine Zeile wählt den Wert. Im aktuellen Schritt gilt er beim Weitergehen, in einem früheren sofort."""
        step = self.shown
        picked = self.table.selection()
        if not picked or step is None or step["key"] in ("final", "gate") or not step["done"]:
            return
        point = step["points"][int(picked[0])]
        if str(point.get("value")) == str(step["selected"]):
            return
        if self.running or point.get("state") != "OK":  # Während einer Messung stehen die Einstellungen der nächsten Punkte schon fest.
            self._fill_table(step)
            return
        step["selected"] = point["value"]
        if step is not self.step:
            self._apply_selection(step, point)
            self._persist(step)
            self.status_var.set(f"{step['flag']} ist jetzt {point.get('label', point['value'])}.")
        self.pick_var.set(self._pick_text(step, best_point(step["key"], step["points"])))
        self._draw(step)

    def next_step(self):
        step = self.step
        if step is None:
            return
        if self.shown is not step:
            self._open_step(step)
            return
        if self.running or step["key"] not in CHOICE_STEPS:
            return
        self._apply_choice(step)
        self._persist(step)
        self._go_on(step)

    def _can_redo(self):
        shown = self.shown
        return shown is not None and shown is not self.step and shown["done"] and shown["key"] not in ("gate", "final") and not self.running

    def redo_from_shown(self):
        """Misst alle Schritte nach dem gezeigten neu. Nötig, wenn eine Änderung den freien VRAM verschiebt und damit die späteren Empfehlungen."""
        if not self._can_redo():
            return
        step = self.shown
        index = self.steps.index(step)
        later = self.steps[index + 1:]
        seconds = sum(point.get("seconds") or 0 for other in later for point in other["points"])
        duration = f"Beim letzten Mal dauerte das etwa {max(1, round(seconds / 60))} Min. " if seconds else ""
        if not messagebox.askokcancel("Ab hier neu messen", "Alle Schritte nach diesem werden noch einmal gemessen, ihre bisherigen Werte und Diagramme fallen weg. "
                                      f"{duration}Deine Wahl in den Schritten bis hier bleibt.", parent=self):
            return
        gate = next((other for other in later if other["key"] == "gate"), None)
        removed = []
        if gate:  # Der freiwillige Teil beginnt von vorn: Du entscheidest neu, was gemessen wird.
            removed = [other for other in later if other["quality"]]
            self.steps = [other for other in self.steps if other not in removed]
        for other in later:
            sweep = other.pop("sweep", None)
            if sweep in self.app._saved_sweeps():
                self.app._saved_sweeps().remove(sweep)
            try:
                Path(other["image"]).unlink(missing_ok=True)
            except OSError as err:
                self.app._log_app(f"Altes Diagramm nicht gelöscht: {err}")
            other.update(points=[], selected=None, done=False, skipped=False)
        self.final_changed = False
        self.final_result = None
        self.final_backup = None
        self.aborted = False
        self.abort_button.configure(text="Abbrechen")
        self.bar.configure(value=0)
        self.app._log_app(f"Optimieren: Ab {step['flag']} wird neu gemessen.")
        self._save_report()
        self._begin(self.steps[index + 1])

    def _apply_selection(self, step, point):
        """Übernimmt einen nachträglich geänderten Wert. Das Ergebnis zeigt danach, dass er nicht mitgemessen wurde."""
        if step["key"] in CHOICE_STEPS:
            self._apply_choice(step)
        else:
            self.current.update({name: value for name, value in point["settings"].items() if name in self._fields(step["key"])})
            self.app._log_app(f"Optimieren: {step['flag']} {point['value']} gewählt.")
        if self.steps[-1]["done"]:
            self.final_changed = True
            self._build_final_rows()
            self._update_command()

    def _apply_choice(self, step):
        key = step["key"]
        point = next((p for p in step["points"] if p["value"] == step["selected"]), None)
        if point is None:
            return
        if key in SWEEP_STEPS:
            self.current["ctx_size"] = point["value"]
            if key == "context":
                # Ein später gemessenes -ncmoe bleibt, auch wenn der Kontext nachträglich wechselt.
                if not any(other["key"] == "ncmoe" and other["done"] for other in self.steps):
                    self.current.update({name: point["settings"][name] for name in ("ngl", "n_cpu_moe")})
                self.base_point = point
        else:
            self.current[self._start_value(key)] = point["value"]
        self.app._log_app(f"Optimieren: {step['flag']} {point['value']} gewählt.")

    def _go_on(self, step):
        index = self.steps.index(step)
        self._begin(self.steps[index + 1])

    def _gate_measure(self):
        chosen = [key for key in QUALITY_STEPS if self.gate_vars[key].get()]
        if not chosen:
            self._gate_skip()
            return
        gate = self.step
        gate["done"] = True
        position = self.steps.index(gate) + 1
        for offset, key in enumerate(chosen):
            step = self._new_step(key)
            step["image"] = str(self.folder / f"{position + offset + 1:02d}_{key}.png")
            self.steps.insert(position + offset, step)
        self.steps[-1]["image"] = str(self.folder / f"{len(self.steps):02d}_final.png")
        self._go_on(gate)

    def _gate_skip(self):
        gate = self.step
        gate["done"] = gate["skipped"] = True
        self._go_on(gate)

    # ---------- Ergebnis ----------

    def remeasure_final(self):
        """Misst die aktuelle Auswahl neu. Nur das Ergebnis, die früheren Schritte bleiben, wie sie sind."""
        final = self.steps[-1]
        if self.running or not final["done"]:
            return
        self.final_backup = {"points": final["points"], "selected": final["selected"]}
        final.update(points=[], selected=None, done=False)
        self.final_changed = False
        self.step = final
        self._measure(final, self._plan(final))

    def _finish_final(self, step):
        step["done"] = True
        self.final_backup = None
        context = int(self.current["ctx_size"])
        measured = next((point for point in step["points"] if point["value"] == "Ergebnis"), None)
        base = next((point for point in step["points"] if point["value"] == "Ausgang"), None)
        if base is None:
            base = self._final_base(context)
        elif base.get("state") == "OK":
            step["baselines"][str(context)] = dict(base)
        size = llama_plot.compact_tokens((measured or {}).get("ctx") or context)
        for point, name in ((base, "Ausgang"), (measured, "Ergebnis")):
            if point:  # Beide Werte gelten für dieselbe Kontextgröße. Das steht dabei, damit die Zahlen nicht größer oder kleiner wirken, als sie sind.
                point.update(value=name, label=f"{name} ({size} Kontext)")
        step["points"] = [point for point in (base, measured) if point]
        step["selected"] = "Ergebnis"
        self.final_result = measured
        self._save_image(step)
        self._save_report()
        self._build_final_rows()
        self._draw_final(step)
        self._update_command()
        self.status_var.set("Fertig. Prüfe die Werte und speichere sie als Profil.")
        self.bar.configure(value=self.bar["maximum"])
        self.abort_button.configure(text="Schließen")
        self._show(step)

    def _draw_final(self, step):
        if self.final_canvas is None and self.figure is not None:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            self.final_figure = Figure(layout="constrained", dpi=96)
            self.final_canvas = FigureCanvasTkAgg(self.final_figure, master=self.final_plot)
            self.final_canvas.get_tk_widget().pack(fill="both", expand=True)
        if self.final_canvas is not None:
            llama_plot.draw_param(self.final_figure, step, compact=True)
            self.final_canvas.draw_idle()

    def _build_final_rows(self):
        for child in self.final_table.winfo_children():
            child.destroy()
        for column, title in enumerate(("Parameter", "Vorher", "Gewählt")):
            ttk.Label(self.final_table, text=title, style="Head.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 4))
        self.final_boxes = {}
        rows = [step for step in self.steps if step["points"] and step["key"] not in ("final",) and not (step["key"] == "context" and any(s["key"] == "c" and s["points"] for s in self.steps))]
        for number, step in enumerate(rows, start=1):
            name = "ctx_size" if step["key"] in SWEEP_STEPS else self._start_value(step["key"])
            before = self.start_values.get(name, "").strip() or "Standard"
            ttk.Label(self.final_table, text=step["flag"], style="Flag.TLabel").grid(row=number, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Label(self.final_table, text=before).grid(row=number, column=1, sticky="w", padx=(0, 8))
            values = [point["value"] for point in ok_points(step["points"])]
            box = ttk.Combobox(self.final_table, values=values, state="readonly", width=20)
            box.set(self.current.get(name, ""))
            box.grid(row=number, column=2, sticky="w", pady=2)
            box.bind("<<ComboboxSelected>>", lambda _event, target=name, widget=box: self._on_final_change(target, widget))
            self.final_boxes[name] = box
        self._update_final_note()

    def _on_final_change(self, name, box):
        self.current[name] = box.get()
        if name == "n_cpu_moe":
            self.current["ngl"] = "all"
        self.final_changed = True
        self._update_command()
        self._update_final_note()

    def _update_final_note(self):
        result = self.final_result
        base = next((point for point in self.steps[-1]["points"] if point.get("value") == "Ausgang"), None)
        parts = []
        if result and result.get("state") == "OK" and base and base.get("tps"):
            parts.append(f"Vergleich bei gleichem Kontext von {llama_plot.compact_tokens(result['ctx'])}.")
            parts.append(f"Antwort: {llama_plot.german_number(base['tps'])} → {llama_plot.german_number(result['tps'])} Tok/s.")
            if base.get("prompt_tps") and result.get("prompt_tps"):
                parts.append(f"Einlesen: {llama_plot.german_number(base['prompt_tps'], 0)} → {llama_plot.german_number(result['prompt_tps'], 0)} Tok/s.")
        elif result and result.get("state") == "OK":
            parts.append(f"Der Ausgangspunkt ließ sich bei {llama_plot.compact_tokens(result['ctx'])} Kontext nicht messen.")
        elif result:
            parts.append(f"Die Schlussmessung ist fehlgeschlagen: {result.get('error', 'unbekannter Fehler')}")
        if self.final_changed:
            parts.append("Die Auswahl hat sich geändert, das Diagramm zeigt noch die vorherige. Drücke „Ergebnis neu messen“.")
        if any(step["quality"] and step["points"] for step in self.steps):
            parts.append("Du hast Parameter gemessen, die die Qualität beeinflussen können. Das Tempo sagt darüber nichts aus.")
        self.final_note.set("   ".join(parts))

    def _update_command(self):
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", self.app.optimize_command(self.exe, self.model, self.current))
        self.command_text.configure(state="disabled")

    def save_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showinfo("Profil", "Bitte einen Profilnamen eintragen.", parent=self)
            return
        if self.app.profile_exists(name) and not messagebox.askokcancel("Profil", f"Das Profil „{name}“ gibt es schon. Überschreiben?", parent=self):
            return
        self.app.save_optimized_profile(name, self.current, self.model)
        self.saved_profile = name
        self._save_report()
        self.status_var.set(f"Als Profil „{name}“ gespeichert und ins Hauptfenster übernommen.")
        self.app._log_app(f"Optimieren: Profil „{name}“ gespeichert.")

    # ---------- Ende ----------

    def open_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        os.startfile(self.folder)

    def abort(self):
        if self.running:
            self.cancel.set()
            self.status_var.set("Wird abgebrochen…")
            process = self.app.sweep_proc
            if process and process.poll() is None:
                process.terminate()
            return
        self.close()

    def close(self, force=False):
        if self.closed:
            return
        finished = self.steps[-1]["done"]
        if self.running and not force and not messagebox.askokcancel("Modell optimieren", "Die Messung läuft noch. Fertige Punkte und Diagramme bleiben gespeichert.", parent=self):
            return
        if finished and not self.saved_profile and not force and not messagebox.askokcancel("Modell optimieren", "Das Ergebnis ist noch nicht als Profil gespeichert. Trotzdem schließen?", parent=self):
            return
        waiting = self.running
        if waiting:
            self.cancel.set()
            process = self.app.sweep_proc
            if process and process.poll() is None:
                process.terminate()
        self.closed = True
        self._save_report()
        self.destroy()
        if not waiting:
            self.app.on_optimizer_closed()
