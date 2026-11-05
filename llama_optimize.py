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
from llama_lang import tr

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

TEXTS = {
    "context": (tr("Kontextgröße"), "-c", tr("Misst das Tempo bei verschiedenen Kontextgrößen. Wähle danach die Größe, die du nutzen möchtest.")),
    "range": (tr("Kontextgröße"), "-c", tr("Wähle den Bereich, in dem gemessen werden soll. Dazwischen verteilt das Programm 7 Messpunkte.")),
    "ncmoe": (tr("MoE-Experten"), "-ncmoe", tr("Sucht, wie viele Expertenschichten in den RAM sollten. Der beste Wert wird automatisch gewählt.")),
    "ub": (tr("Micro-Batch"), "-ub", tr("Sucht das schnellste Einlesen, ohne dass das Antworttempo leidet. Der beste Wert wird automatisch gewählt.")),
    "spec": (tr("Spekulation"), "--spec-type", tr("Misst neuen und wiederholten Text. Gewählt wird das Schnellste bei wiederholtem Text, das neuen Text höchstens 5 % kostet.")),
    "gate": (tr("Qualität"), "", ""),
    "ctk": (tr("KV-Cache K"), "-ctk", tr("Kann die Qualität beeinflussen. Wähle den Typ, den du nutzen möchtest.")),
    "ctv": (tr("KV-Cache V"), "-ctv", tr("Kann die Qualität beeinflussen. Wähle den Typ, den du nutzen möchtest.")),
    "result": (tr("Ergebnis"), "-c", tr("Misst alle Kontextgrößen mit den gewählten Einstellungen und vergleicht sie mit der Messung vom Anfang. Wähle rechts die Größe, die du nutzen möchtest.")),
}
MEANINGS = {
    "context": tr("Wie viele Tokens das Modell gleichzeitig sehen kann: Verlauf, Dateien und die aktuelle Antwort."),
    "ncmoe": tr("Bei wie vielen Schichten die Experten im RAM liegen. Die übrigen rechnen schneller im VRAM."),
    "ub": tr("Wie viele Tokens auf einmal berechnet werden. Mehr liest lange Prompts schneller ein, braucht aber VRAM."),
    "spec": tr("Sagt die nächsten Tokens voraus und lässt das Modell sie gemeinsam prüfen. Das beschleunigt wiederholten Text."),
    "gate": tr("Freiwillig: Die folgenden Parameter können die Antworten verändern."),
    "ctk": tr("Datentyp der K-Werte im Kontextspeicher. Kleinere Typen sparen VRAM, sind aber ungenauer."),
    "ctv": tr("Datentyp der V-Werte im Kontextspeicher. Sie wirken stärker auf das Ergebnis als die K-Werte."),
    "result": tr("Misst alle Kontextgrößen noch einmal mit den gewählten Einstellungen und vergleicht sie mit dem Anfang."),
}
QUALITY_STEPS = ("ctk", "ctv")
SWEEP_STEPS = ("context", "result")
CHOICE_STEPS = ("context", "ctk", "ctv", "result")


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
        raise ValueError(tr("„{strip}“ ist keine Kontextgröße. Beispiele: 16k, 32768, 260k.", strip=text.strip()))
    return int(match.group(1).replace(".", "")) * (1024 if match.group(2) else 1)


def read_range(low_text, high_text, trained):
    """Prüft den Kontext-Bereich gegen das Modell. Liefert (von, bis, Hinweis). Liegt „bis“ darüber, gilt das Maximum des Modells."""
    low, high = parse_tokens(low_text), parse_tokens(high_text)
    if low < MIN_CONTEXT:
        raise ValueError(tr("Der Kontext muss mindestens {min_context} Tokens groß sein.", min_context=llama_plot.format_number(MIN_CONTEXT, 0)))
    if trained and low >= trained:
        raise ValueError(tr("„Von“ muss unter dem Maximum dieses Modells von {trained} Tokens liegen.", trained=llama_plot.format_number(trained, 0)))
    note = ""
    if trained and high > trained:
        high = trained
        note = tr("Das Modell unterstützt höchstens {trained} Tokens, deshalb wird nur bis dahin gemessen.", trained=llama_plot.format_number(trained, 0))
    if high <= low:
        raise ValueError(tr("„Bis“ muss größer sein als „Von“."))
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
    def __init__(self, app, exe, model, info, values, free, folder, resume=None):
        super().__init__(app)
        self.app = app
        self.exe = exe
        self.model = model
        self.info = info
        self.start_values = dict(values)
        self.current = dict(values)
        self.free = free
        self.folder = Path(folder)
        self.stamp = time.strftime(tr("%d.%m.%Y %H:%M"))
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
        self.final_backup = None
        self.range = None
        self.range_default = app.optimize_range_default(self.current)
        self.title(tr("Modell optimieren: {stem}", stem=model.stem))
        self.geometry(f"{min(WINDOW_WIDTH, self.winfo_screenwidth() - 40)}x{min(WINDOW_HEIGHT, self.winfo_screenheight() - 80)}")  # So breit, dass die Balkenbeschriftungen lesbar sind.
        self.minsize(1000, 780)  # Darunter passen nicht mehr alle Schritte samt Erklärung in die linke Liste.
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        if resume:
            self._restore(resume)
            return
        for key in ("context", "ncmoe", "ub", "spec", "gate", "result"):
            if key != "ncmoe" or info.get("experts"):  # Normale Modelle haben keine Experten, die sich verteilen ließen.
                self.steps.append(self._new_step(key))
        self._refresh_list()
        self._begin(self.steps[0])

    def _restore(self, report):
        """Setzt einen gespeicherten Lauf fort. Fertige Schritte bleiben, ein abgebrochener Schritt beginnt von vorn.
        Ältere Läufe enthalten noch „Kontext erneut“ und das alte Ergebnis. Beides misst das neue Ergebnis ohnehin neu."""
        saved = {step["key"]: step for step in report.get("steps", [])}
        self.start_values.update(report.get("start") or {})
        self.current.update(report.get("current") or {})
        self.stamp = report.get("time") or self.stamp
        self.saved_profile = report.get("profile") or ""
        later = any(key in saved for key in ("ctk", "ctv", "c", "final", "result"))
        for key in ("context", "ncmoe", "ub", "spec", "gate", "ctk", "ctv", "result"):
            if key == "ncmoe" and not self.info.get("experts"):
                continue
            if key in QUALITY_STEPS and key not in saved:
                continue
            step = self._new_step(key)
            data = saved.get(key)
            if data:
                step.update({name: data[name] for name in ("points", "selected", "done", "skipped", "image") if name in data})
            elif key == "gate" and later:  # Der Qualitätsschritt hat selbst keine Messwerte und steht in älteren Läufen nicht in der Datei.
                step["done"] = True
                step["skipped"] = not any(name in saved for name in QUALITY_STEPS)
            self.steps.append(step)
        context = self.steps[0]
        requested = [point["requested"] for point in context["points"]]
        self.range = (min(requested), max(requested))
        sweeps = self.app._saved_sweeps()
        for step in self.steps:
            if step["key"] not in SWEEP_STEPS or not step["points"]:
                continue
            sweep = next((item for item in sweeps if item.get("image") == step["image"]), None)
            if sweep is None:
                sweep = {"time": self.stamp, "model": self.model.stem, "settings": self.app.optimize_describe(self.current), "fit_limit": self.app.optimize_limit(self.current),
                         "free": self.free, "contexts": sorted(point["requested"] for point in step["points"]), "complete": True, "image": step["image"]}
            sweep["points"] = step["points"]  # Dieselbe Liste wie im Schritt, damit Nachmessungen in beiden erscheinen.
            if step["key"] == "result":
                sweep["baseline"] = context["points"]
            step["sweep"] = sweep
        self.app._log_app(tr("Optimieren: Lauf vom {time} wird fortgesetzt.", time=self.stamp))
        self._refresh_list()
        waiting = next((step for step in self.steps if not step["done"]), None)
        if waiting is None:  # Alles war schon fertig gemessen: Es geht beim Ergebnis weiter, dort lässt sich wählen und speichern.
            self.step = self.steps[-1]
            self._finish_context(self.steps[-1])
            return
        waiting.update(points=[], selected=None, skipped=False)
        waiting.pop("sweep", None)
        self.status_var.set(tr("Lauf vom {time} fortgesetzt. Die gemessenen Schritte bleiben.", time=self.stamp))
        self._begin(waiting)

    # ---------- Aufbau ----------

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        side = ttk.Frame(self, padding=(10, 10, 6, 10))
        side.grid(row=0, column=0, sticky="ns")
        ttk.Label(side, text=tr("Schritte"), style="Head.TLabel").pack(anchor="w")
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
        self._build_finish(main)

        status = ttk.Frame(main)
        status.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.bar = ttk.Progressbar(status, length=170)
        self.bar.pack(side="left")
        self.status_var = tk.StringVar()
        ttk.Label(status, textvariable=self.status_var).pack(side="left", padx=(10, 0))

        nav = ttk.Frame(main)
        nav.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.abort_button = ttk.Button(nav, text=tr("Abbrechen"), style="Big.TButton", command=self.abort)
        self.abort_button.pack(side="left")
        ttk.Button(nav, text=tr("Diagramme öffnen"), style="Big.TButton", command=self.open_folder).pack(side="left", padx=(6, 0))
        self.next_button = ttk.Button(nav, text=tr("Weiter"), style="Big.TButton", command=self.next_step, state="disabled")
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
        maximum = tr("Dieses Modell unterstützt bis zu {trained} Tokens Kontext.", trained=llama_plot.format_number(trained, 0)) if trained else tr("Die Modelldatei nennt keine Obergrenze für den Kontext.")
        ttk.Label(page, text=maximum, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(page)
        row.pack(anchor="w", pady=(10, 0))
        self.range_low_var = tk.StringVar(value=str(low))
        self.range_high_var = tk.StringVar(value=str(high))
        for label, var in ((tr("Von"), self.range_low_var), (tr("Bis"), self.range_high_var)):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 6))
            ttk.Entry(row, textvariable=var, width=12, font=("Segoe UI", 11)).pack(side="left", padx=(0, 16))
            var.trace_add("write", lambda *_: self._update_range())
        ttk.Label(row, text=tr("zum Beispiel 16k, 32768 oder 260k"), style="Hint.TLabel").pack(side="left")
        self.range_points_var = tk.StringVar()
        ttk.Label(page, textvariable=self.range_points_var, justify="left").pack(anchor="w", pady=(12, 0))
        self.range_note_var = tk.StringVar()
        self.range_note = ttk.Label(page, textvariable=self.range_note_var, justify="left", wraplength=800)
        self.range_note.pack(anchor="w", pady=(6, 0))
        self.start_button = ttk.Button(page, text=tr("Messung starten"), style="Big.TButton", command=self.start_range)
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
        self.range_points_var.set(tr("Messpunkte: ") + ", ".join(llama_plot.compact_tokens(value) for value in points))
        notes = [note] if note else []
        if limit and low < limit < high:
            notes.append(tr("Laut Schätzung passt alles bis etwa {limit} in den VRAM. Dieser Wert ist als Messpunkt dabei.", limit=llama_plot.compact_tokens(limit)))
        elif limit and limit >= high:
            notes.append(tr("Laut Schätzung passt der ganze Bereich in den VRAM."))
        elif limit is not None:
            notes.append(tr("Laut Schätzung passt schon der kleinste Wert nicht ganz in den VRAM."))
        if low < FULL_PROMPT_CONTEXT:
            notes.append(tr("Unter {full_prompt_context} Tokens wird der Prompt beim Einlesen kürzer. Diese Werte sind nicht "
                "ganz vergleichbar.", full_prompt_context=llama_plot.format_number(FULL_PROMPT_CONTEXT, 0)))
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
        columns = {"value": (tr("Wert"), 160), "prompt_tps": (tr("Prompt Tok/s"), 84), "tps": ("Tok/s", 52), "repeat_tps": (tr("Wiederholt"), 72), "vram": (tr("VRAM MiB"), 66), "state": (tr("Status"), 54)}
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
        self.redo_button = ttk.Button(table, text=tr("Ab hier neu messen"), style="Big.TButton", command=self.redo_from_shown)
        self.redo_button.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.redo_button.grid_remove()
        self.params = ttk.Frame(table)
        self.params.grid(row=4, column=0, sticky="w", pady=(14, 0))
        self.params.grid_remove()
        self._ensure_canvas()

    def _build_gate_page(self):
        self.gate_page = page = ttk.Frame(self.body)
        self.gate_warning = warning = tk.Label(page, background=PANEL_BACKGROUND, justify="left", anchor="w", wraplength=640, padx=12, pady=10, font=("Segoe UI", 10),
                                               text=tr("Achtung: Die nächsten Parameter können die Qualität der Antworten beeinflussen. Kleinere KV-Cache-Typen sparen VRAM, speichern "
                                "den Kontext aber ungenauer. Die Messung zeigt nur das "
                                "Tempo, nicht die Qualität. Prüfe das Ergebnis deshalb mit deinen eigenen Aufgaben."))
        warning.pack(fill="x")
        ttk.Label(page, text=tr("Was soll gemessen werden?"), style="Head.TLabel").pack(anchor="w", pady=(12, 4))
        self.gate_vars = {}
        for key, text in (("ctk", tr("-ctk: KV-Cache-Typ K")), ("ctv", tr("-ctv: KV-Cache-Typ V"))):
            self.gate_vars[key] = tk.BooleanVar(value=True)
            ttk.Checkbutton(page, text=text, variable=self.gate_vars[key]).pack(anchor="w", pady=2)
        row = ttk.Frame(page)
        row.pack(anchor="w", pady=(12, 0))
        ttk.Button(row, text=tr("Ausgewählte messen"), style="Big.TButton", command=self._gate_measure).pack(side="left")
        ttk.Button(row, text=tr("Überspringen"), style="Big.TButton", command=self._gate_skip).pack(side="left", padx=(6, 0))

    def _build_finish(self, main):
        """Hinweis, Startbefehl und Profil unter dem fertigen Ergebnis."""
        self.finish = page = ttk.Frame(main)
        page.grid(row=3, column=0, sticky="ew")
        page.columnconfigure(0, weight=1)
        page.grid_remove()
        self.final_note = tk.StringVar()
        self.final_note_label = ttk.Label(page, textvariable=self.final_note, wraplength=650, justify="left")
        self.final_note_label.grid(row=0, column=0, sticky="w", pady=(6, 0))
        self.command_text = tk.Text(page, height=3, wrap="word", font=("Consolas", 9), state="disabled", background="#f4f4f4", relief="flat", padx=6, pady=4)
        self.command_text.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        row = ttk.Frame(page)
        row.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(row, text=tr("Profilname")).pack(side="left", padx=(0, 6))
        self.profile_var = tk.StringVar(value=self.model.stem)
        ttk.Entry(row, textvariable=self.profile_var, width=46).pack(side="left")
        self.save_button = ttk.Button(row, text=tr("Als Profil speichern"), style="Big.TButton", command=self.save_profile)
        self.save_button.pack(side="left", padx=(8, 0))
        self.remeasure_button = ttk.Button(row, text=tr("Ergebnis neu messen"), style="Big.TButton", command=self.remeasure_final)
        self.remeasure_button.pack(side="left", padx=(8, 0))

    def _ensure_canvas(self):
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            ttk.Label(self.plot, justify="left", text=tr("Für die Diagramme fehlt matplotlib: py -m pip install matplotlib\nDie Messwerte stehen trotzdem in der Tabelle.")).pack(anchor="nw")
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
        for candidate in (self.range_page, self.measure_page, self.gate_page):
            if candidate is page:
                candidate.grid(row=0, column=0, sticky="nsew")
            else:
                candidate.grid_remove()

    def _last_done(self, step):
        """Das Ergebnis ist fertig. Dann gibt es Befehl und Profil."""
        return step is self.steps[-1] and step["done"] and step["key"] == "result" and bool(step["points"])

    def _show(self, step):
        self.shown = step
        self._refresh_list()
        if self._last_done(step) and not self.running:
            self.finish.grid()
        else:
            self.finish.grid_remove()
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
            self.head_var.set(tr("Parameter, die die Qualität beeinflussen können"))
            self.explain_var.set(tr("Dieser Teil ist freiwillig."))
            self._show_page(self.gate_page)
            return
        if step["key"] == "result" and step.get("sweep", {}).get("baseline"):
            self.explain_var.set(tr("Gestrichelt: die Messung vom Anfang mit den Ausgangswerten. Durchgezogen: mit den gewählten Einstellungen. "
                                    "Wähle rechts die Kontextgröße, die du nutzen möchtest."))
        self._show_page(self.measure_page)
        self._fill_table(step)
        self._draw(step)
        self._show_params(step)

    def _show_params(self, step):
        """Vorher und gewählt für jeden gemessenen Parameter. Nur im fertigen Ergebnis, dort gilt die Auswahl."""
        for child in self.params.winfo_children():
            child.destroy()
        if not self._last_done(step):
            self.params.grid_remove()
            return
        self.params.grid()
        for column, title in enumerate((tr("Parameter"), tr("Vorher"), tr("Gewählt"))):
            ttk.Label(self.params, text=title, style="Head.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 14), pady=(0, 4))
        rows = [("-c", "ctx_size")] + [(other["flag"], self._start_value(other["key"])) for other in self.steps
                                       if other["points"] and other["key"] not in ("context", "gate", "result")]
        for number, (flag, name) in enumerate(rows, start=1):
            before = str(self.start_values.get(name, "")).strip() or tr("Standard")
            chosen = str(self.current.get(name, "")).strip() or tr("Standard")
            ttk.Label(self.params, text=flag, style="Flag.TLabel").grid(row=number, column=0, sticky="w", padx=(0, 14), pady=1)
            ttk.Label(self.params, text=before).grid(row=number, column=1, sticky="w", padx=(0, 14))
            ttk.Label(self.params, text=chosen, font=("Segoe UI", 9, "bold") if chosen != before else None).grid(row=number, column=2, sticky="w")

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
            button.configure(text=tr("Zurück zum aktuellen Schritt"), state="normal")
            return
        if step is self.steps[-1] and step["done"]:
            button.pack_forget()
            return
        button.pack(side="right")
        waiting = step["key"] in CHOICE_STEPS and step["done"] and not self.running and not self.aborted
        best = best_point(step["key"], step["points"]) if waiting else None
        if best:
            button.configure(text=tr("Auswahl übernehmen und weiter"), state="normal")
        elif waiting and step["key"] != "context":
            button.configure(text=tr("Weiter ohne Änderung"), state="normal")
        else:
            button.configure(text=tr("Weiter"), state="disabled")

    def _fill_table(self, step):
        self.table.delete(*self.table.get_children())
        best = best_point(step["key"], step["points"])
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
        if best:
            lines.append(tr("★ Empfehlung: {get}", get=best.get('label', best.get('value'))))
        if step["selected"] is not None:
            lines.append(tr("Gewählt: {label}", label=self._label(step, step['selected'])))
        if step["key"] != "gate" and step["done"] and not self.running:
            lines.append(tr("Zum Ändern eine Zeile anklicken."))
            later = self.steps[self.steps.index(step) + 1:]
            if any(other["points"] for other in later):
                lines.append(tr("Die späteren Schritte stammen noch vom vorherigen Wert. „Ab hier neu messen“ wiederholt sie."))
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
        return settings, context, key == "spec"

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
        if key == "result":
            # Dieselben Kontexte wie am Anfang, damit sich jede Größe direkt vergleichen lässt.
            chosen = int(self.current["ctx_size"])
            planned = {point["requested"] for point in self.steps[0]["points"]} | {chosen}
            limit = self.app.optimize_limit(self.current)  # Kleinere KV-Typen verschieben die VRAM-Grenze, dort knickt das Tempo meist ab.
            low, high = self.range
            if limit and low < limit < high and all(max(limit, value) > min(limit, value) * RANGE_MIN_STEP for value in planned):
                planned.add(limit)
            return sorted(planned)
        raise ValueError(key)

    def _start_value(self, key):
        return {"ncmoe": "n_cpu_moe", "ub": "ubatch", "spec": "spec_type", "ctk": "cache_k", "ctv": "cache_v"}.get(key)

    def _begin(self, step):
        self.step = step
        if self.range is None:
            self.status_var.set(tr("Wähle den Bereich und starte die Messung."))
            self._show(step)
            return
        if step["key"] == "gate":
            self.status_var.set(tr("Wähle, was gemessen werden soll, oder überspringe den Schritt."))
            self._show(step)
            return
        if step["key"] in SWEEP_STEPS:
            step["sweep"] = {"time": self.stamp, "model": self.model.stem, "settings": self.app.optimize_describe(self.current), "fit_limit": self.app.optimize_limit(self.current),
                             "free": self.free, "contexts": [], "points": step["points"], "complete": False, "image": step["image"]}
            if step["key"] == "result":
                step["sweep"]["baseline"] = self.steps[0]["points"]  # Dieselbe Liste: Nachgemessene Vergleichspunkte erscheinen sofort im Diagramm.
        self._measure(step, self._plan(step))

    def _measure(self, step, values):
        jobs = []
        if step["key"] == "result":
            # Fehlt am Anfang eine Kontextgröße, etwa die neue VRAM-Grenze, wird sie mit den Ausgangswerten nachgemessen. Sonst gäbe es dort keinen Vergleich.
            base = self.steps[0]
            known = {point["requested"] for point in base["points"]}
            for value in values:
                if value not in known:
                    settings = dict(self.start_values)
                    settings.update(self._moe_settings(settings, value))
                    jobs.append((value, settings, value, False, base))
        for value in values:
            settings, context, repeat = self._settings(step, value)
            jobs.append((value, settings, context, repeat, step))
        if step["key"] in SWEEP_STEPS:
            step["sweep"]["contexts"] = sorted(set(step["sweep"]["contexts"]) | {job[0] for job in jobs if job[4] is step})
        self.running = True
        self.cancel = threading.Event()
        done = len(step["points"])
        self.measure_total = done + len(jobs)
        self.bar.configure(value=done, maximum=max(self.measure_total, 1))
        self.abort_button.configure(text=tr("Abbrechen"))
        self._show(step)
        threading.Thread(target=self._worker, args=(step, jobs, done, self.cancel), daemon=True).start()

    def _worker(self, step, jobs, done, cancel):
        for index, (value, settings, context, repeat, target) in enumerate(jobs):
            if cancel.is_set():
                break
            started = time.perf_counter()
            position = done + index + 1

            def announce(phase, shown=tr(value) if isinstance(value, str) else value, at=position):
                self.app.events.put(lambda: self._on_phase(target, shown, at, phase))

            point = self.app._measure_context(self.exe, self.model, settings, context, cancel, announce, repeat)
            if cancel.is_set():
                break
            point["value"] = str(value)
            point["label"] = llama_plot.compact_tokens(int(value)) if target["key"] in SWEEP_STEPS else tr(str(value))
            point["settings"] = {key: settings.get(key, "") for key in ("ngl", "n_cpu_moe", "ubatch", "batch", "spec_type", "cache_k", "cache_v")}
            point["seconds"] = round(time.perf_counter() - started, 1)
            self.app.events.put(lambda p=point, at=position, into=target: self._on_point(into, p, at))
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
            remaining = tr("   ·   noch etwa {value} Min. in diesem Schritt", value=max(1, round(minutes)))
        shown = llama_plot.compact_tokens(int(value)) if step["key"] in SWEEP_STEPS else value
        self.status_var.set(tr("{texts}, Punkt {at} von {measure_total}: {shown}, {phase}{remaining}", texts=TEXTS[step['key']][0], at=at, measure_total=self.measure_total, shown=shown, phase=phase, remaining=remaining))

    def _on_point(self, step, point, at):
        if not self._alive():
            return
        step["points"].append(point)
        if step["key"] in SWEEP_STEPS:  # Nachgemessene Punkte reihen sich nach der Größe ein, sonst springt die Linie im Diagramm.
            step["points"].sort(key=lambda item: item["requested"])
        self.bar.configure(value=at)
        if point.get("error"):
            self.app._log_app(tr("Optimieren, {flag} {value}: {error}", flag=step['flag'], value=point['value'], error=point['error']))
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
            self.app._log_app(tr("Für das Diagramm fehlt matplotlib: py -m pip install matplotlib."))
        except (OSError, ValueError) as err:
            self.app._log_app(tr("Diagramm nicht gespeichert: {err}", err=err))

    def _save_report(self):
        report = {"model": self.model.stem, "time": self.stamp, "start": self.start_values, "current": self.current, "profile": self.saved_profile,
                  "steps": [{key: value for key, value in step.items() if key != "sweep"} for step in self.steps if step["points"] or step["done"]]}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "ergebnis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as err:
            self.app._log_app(tr("Ergebnis nicht gespeichert: {err}", err=err))

    def _on_measured(self, step, cancelled):
        if self.closed:  # Erst jetzt ist der Testserver weg und sein VRAM frei.
            self.app.on_optimizer_closed()
            return
        self.running = False
        if cancelled and step is self.steps[-1] and self.final_backup:
            # Das letzte fertige Ergebnis bleibt, es passt nur nicht mehr zur Auswahl.
            self._forget_sweep(step)
            step.update(self.final_backup, done=True)
            if step.get("sweep"):
                self.app._store_sweep(step["sweep"])
            self.final_backup = None
            self.final_changed = True
            self.abort_button.configure(text=tr("Schließen"))
            self._update_final_note()
            self._show(step)
            self.status_var.set(tr("Neue Messung abgebrochen. Das Diagramm zeigt noch das vorherige Ergebnis."))
            return
        if cancelled:
            self.status_var.set(tr("Abgebrochen. Die fertigen Messpunkte und Diagramme bleiben gespeichert."))
            self.abort_button.configure(text=tr("Schließen"))
            self.aborted = True
            self._update_nav()
            return
        key = step["key"]
        if key == "ncmoe":
            more = moe_more(step["points"], self.info["expert_layers"])
            if more:  # Das Beste lag am Rand, die Suche geht in dieser Richtung weiter.
                self._measure(step, more)
                return
        best = best_point(key, step["points"])
        step["done"] = True
        if key in CHOICE_STEPS:
            self._await_choice(step, best)
            return
        if best is None:
            self.status_var.set(tr("{texts}: Kein Messpunkt war erfolgreich, der bisherige Wert bleibt.", texts=TEXTS[key][0]))
            self.app._log_app(tr("Optimieren: {flag} ohne Ergebnis, der bisherige Wert bleibt.", flag=step['flag']))
        else:
            step["selected"] = best["value"]
            self.current.update({name: value for name, value in best["settings"].items() if name in self._fields(key)})
            self.app._log_app(tr("Optimieren: {flag} {value} gewählt.", flag=step['flag'], value=best['value']))
        self._persist(step)
        self._fill_table(step)
        self._go_on(step)

    @staticmethod
    def _fields(key):
        return {"ncmoe": ("ngl", "n_cpu_moe"), "ub": ("ubatch", "batch"), "spec": ("spec_type",), "ctk": ("cache_k",), "ctv": ("cache_v",)}.get(key, ())

    def _await_choice(self, step, best):
        """Bei den Kontextgrößen ist die Empfehlung vorgewählt. Bei KV-Typen bleibt der bisherige Wert, weil sie die Qualität berühren."""
        key = step["key"]
        keep = self.current.get(self._start_value(key) or "", "").strip() or "f16" if key in ("ctk", "ctv") else self.current.get("ctx_size", "") if key == "result" else None
        measured = {point["value"] for point in ok_points(step["points"])}
        if keep in measured:
            step["selected"] = keep
        else:
            step["selected"] = best["value"] if best else None
        self._fill_table(step)
        self._update_nav()
        if best is None:
            self.status_var.set(tr("Kein Messpunkt war erfolgreich. Prüfe das Server-Log und die Einstellungen."))
            if key == "context":
                self.abort_button.configure(text=tr("Schließen"))
            return
        if step is self.steps[-1]:
            self._finish_context(step)
            return
        self.status_var.set(tr("Fertig gemessen. Wähle in der Tabelle den gewünschten Wert und gehe weiter."))

    def _on_table_pick(self):
        """Eine Zeile wählt den Wert. Im aktuellen Schritt gilt er beim Weitergehen, in einem früheren sofort."""
        step = self.shown
        picked = self.table.selection()
        if not picked or step is None or step["key"] == "gate" or not step["done"]:
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
            self.status_var.set(tr("{flag} ist jetzt {get}.", flag=step['flag'], get=point.get('label', point['value'])))
        elif step is self.steps[-1]:  # Nach dem letzten Kontext kommt nichts mehr, die Wahl gilt sofort.
            self._apply_choice(step)
            self._persist(step)
            self._update_command()
            self._update_final_note()
            self._show_params(step)
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
        return shown is not None and shown is not self.step and shown["done"] and shown["key"] not in ("gate", "result") and not self.running

    def redo_from_shown(self):
        """Misst alle Schritte nach dem gezeigten neu. Nötig, wenn eine Änderung den freien VRAM verschiebt und damit die späteren Empfehlungen."""
        if not self._can_redo():
            return
        step = self.shown
        index = self.steps.index(step)
        later = self.steps[index + 1:]
        seconds = sum(point.get("seconds") or 0 for other in later for point in other["points"])
        duration = tr("Beim letzten Mal dauerte das etwa {value} Min. ", value=max(1, round(seconds / 60))) if seconds else ""
        if not messagebox.askokcancel(tr("Ab hier neu messen"), tr("Alle Schritte nach diesem werden noch einmal gemessen, ihre bisherigen Werte und Diagramme fallen "
            "weg. {duration}Deine Wahl in den Schritten bis hier bleibt.", duration=duration), parent=self):
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
                self.app._log_app(tr("Altes Diagramm nicht gelöscht: {err}", err=err))
            other.update(points=[], selected=None, done=False, skipped=False)
        self.final_changed = False
        self.final_backup = None
        self.aborted = False
        self.abort_button.configure(text=tr("Abbrechen"))
        self.bar.configure(value=0)
        self.app._log_app(tr("Optimieren: Ab {flag} wird neu gemessen.", flag=step['flag']))
        self._save_report()
        self._begin(self.steps[index + 1])

    def _apply_selection(self, step, point):
        """Übernimmt einen nachträglich geänderten Wert. Das Ergebnis zeigt danach, dass er nicht mitgemessen wurde."""
        if step["key"] in CHOICE_STEPS:
            self._apply_choice(step)
        else:
            self.current.update({name: value for name, value in point["settings"].items() if name in self._fields(step["key"])})
            self.app._log_app(tr("Optimieren: {flag} {value} gewählt.", flag=step['flag'], value=point['value']))
        if self.steps[-1]["done"]:
            self.final_changed = True
            self._update_command()
            self._update_final_note()

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
        self.app._log_app(tr("Optimieren: {flag} {value} gewählt.", flag=step['flag'], value=point['value']))

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
        self.steps[-1]["image"] = str(self.folder / f"{len(self.steps):02d}_result.png")
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
        self.final_backup = {"points": final["points"], "selected": final["selected"], "sweep": final["sweep"]}
        self._forget_sweep(final)
        final.update(points=[], selected=None, done=False)
        self.final_changed = False
        self.step = final
        self._begin(final)

    def _forget_sweep(self, step):
        """Nimmt eine überholte Kontextmessung aus dem Verlauf des Kontext-Tests."""
        sweep = step.get("sweep")
        saved = self.app._saved_sweeps()
        if sweep in saved:
            saved.remove(sweep)

    def _finish_context(self, step):
        """Im Ergebnis gilt die Auswahl sofort, es kommt kein Schritt mehr. Darunter stehen Befehl und Profil."""
        self.final_backup = None
        self.final_changed = False
        self._apply_choice(step)
        self._persist(step)
        self._update_command()
        self._update_final_note()
        self.status_var.set(tr("Fertig. Wähle rechts die Kontextgröße und speichere sie als Profil."))
        self.bar.configure(value=self.bar["maximum"])
        self.abort_button.configure(text=tr("Schließen"))
        self._show(step)

    def _context_note(self, step):
        """Vergleich bei der gewählten Kontextgröße, sofern sie auch am Anfang gemessen wurde."""
        result = next((point for point in ok_points(step["points"]) if point["value"] == step["selected"]), None)
        if result is None:
            return []
        base = next((point for point in ok_points(self.steps[0]["points"]) if point["ctx"] == result["ctx"]), None)
        size = llama_plot.compact_tokens(result["ctx"])
        if base is None:
            return [tr("Für {ctx} gibt es keine Messung mit den Ausgangswerten. Das Diagramm zeigt die Werte daneben.", ctx=size)]
        parts = [tr("Vergleich bei gleichem Kontext von {ctx}.", ctx=size),
                 tr("Antwort: {tps} → {tps2} Tok/s.", tps=llama_plot.format_number(base['tps']), tps2=llama_plot.format_number(result['tps']))]
        if base.get("prompt_tps") and result.get("prompt_tps"):
            parts.append(tr("Einlesen: {prompt_tps} → {prompt_tps2} Tok/s.", prompt_tps=llama_plot.format_number(base['prompt_tps'], 0),
                            prompt_tps2=llama_plot.format_number(result['prompt_tps'], 0)))
        return parts

    def _update_final_note(self):
        parts = self._context_note(self.steps[-1])
        if self.final_changed:
            parts.append(tr("Eine frühere Auswahl hat sich geändert, das Diagramm zeigt noch die vorherige. Drücke „Ergebnis neu messen“."))
        if any(step["quality"] and step["points"] for step in self.steps):
            parts.append(tr("Du hast Parameter gemessen, die die Qualität beeinflussen können. Das Tempo sagt darüber nichts aus."))
        self.final_note.set("   ".join(parts))

    def _update_command(self):
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", self.app.optimize_command(self.exe, self.model, self.current))
        self.command_text.configure(state="disabled")

    def save_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showinfo(tr("Profil"), tr("Bitte einen Profilnamen eintragen."), parent=self)
            return
        if self.app.profile_exists(name) and not messagebox.askokcancel(tr("Profil"), tr("Das Profil „{name}“ gibt es schon. Überschreiben?", name=name), parent=self):
            return
        self.app.save_optimized_profile(name, self.current, self.model)
        self.saved_profile = name
        self._save_report()
        self.status_var.set(tr("Als Profil „{name}“ gespeichert und ins Hauptfenster übernommen.", name=name))
        self.app._log_app(tr("Optimieren: Profil „{name}“ gespeichert.", name=name))

    # ---------- Ende ----------

    def open_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        os.startfile(self.folder)

    def abort(self):
        if self.running:
            self.cancel.set()
            self.status_var.set(tr("Wird abgebrochen…"))
            process = self.app.sweep_proc
            if process and process.poll() is None:
                process.terminate()
            return
        self.close()

    def close(self, force=False):
        if self.closed:
            return
        finished = self.steps[-1]["done"]
        if self.running and not force and not messagebox.askokcancel(tr("Modell optimieren"), tr("Die Messung läuft noch. Fertige Punkte und Diagramme bleiben gespeichert."), parent=self):
            return
        if finished and not self.saved_profile and not force and not messagebox.askokcancel(tr("Modell optimieren"), tr("Das Ergebnis ist noch nicht als Profil gespeichert. Trotzdem schließen?"), parent=self):
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
