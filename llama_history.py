"""Historie der Messungen: Läufe von „Modell optimieren“ samt aller Schritte und einzelne Kontext-Tests, neueste zuerst."""

import json
import re
from pathlib import Path

from llama_lang import tr

STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}")
SWEEP_KEYS = ("context", "result", "c")  # „c“ stammt aus älteren Läufen („Kontext erneut“).


def _stamp(name):
    """Sortierbare Zeit aus einem Datei- oder Ordnernamen wie Modell_2026-11-19_14-04-18."""
    match = STAMP.search(name)
    return f"{match.group(1)} {match.group(2)}:{match.group(3)}" if match else ""


def load_runs(folder):
    """Alle gespeicherten Läufe von „Modell optimieren“. Unlesbare Ordner werden übersprungen."""
    runs = []
    for path in Path(folder).glob("*/ergebnis.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        steps = [step for step in report.get("steps", []) if step.get("points")]
        if not steps:
            continue
        runs.append({"kind": "run", "folder": path.parent, "report": report, "steps": steps, "stamp": _stamp(path.parent.name),
                     "label": tr("{time}   ·   {model}   ·   Modell optimieren, {count} Schritte", time=_stamp(path.parent.name), model=report.get("model", "?"),
                                 count=len(steps)) if len(steps) != 1 else
                     tr("{time}   ·   {model}   ·   Modell optimieren, 1 Schritt", time=_stamp(path.parent.name), model=report.get("model", "?"))})
    return runs


def entries(folder, sweeps):
    """Läufe und einzelne Kontext-Tests, neueste zuerst. Kontext-Schritte aus einem Lauf erscheinen nur dort, nicht doppelt."""
    found = load_runs(folder)
    for sweep in sweeps:
        image = sweep.get("image", "")
        if "optimierung" in Path(image).parts:
            continue
        state = "" if sweep.get("complete", True) else tr("   (unvollständig)")
        found.append({"kind": "sweep", "sweep": sweep, "stamp": _stamp(Path(image).name),
                      "label": tr("{time}   ·   {model}   ·   Kontext-Test, {count} Punkte{state}", time=_stamp(Path(image).name) or sweep.get("time", ""),
                                  model=sweep.get("model", "?"), count=len(sweep.get("points", [])), state=state)})
    return sorted(found, key=lambda entry: entry["stamp"], reverse=True)


def step_label(step):
    flag = step.get("flag") or ""
    return f"{step.get('title') or step.get('key')}" + (f"   ({flag})" if flag else "")


def step_sweep(run, step):
    """Baut für einen Kontext-Schritt die Daten für das Kurvendiagramm. Im Ergebnis liegt die Messung vom Anfang gestrichelt dahinter."""
    report = run["report"]
    context = next((other for other in run["steps"] if other.get("key") == "context"), None)
    sweep = {"points": step["points"], "model": report.get("model", ""), "time": report.get("time", ""), "complete": True}
    if step.get("key") in ("result", "c") and context and context is not step:
        sweep["baseline"] = context["points"]
        selected = next((point for point in step["points"] if str(point.get("value")) == str(step.get("selected"))), None)
        if selected:
            sweep["focus"] = selected.get("ctx")
    return sweep


def run_summary(run):
    """Die gewählten Werte eines Laufs in einer Zeile, wie sie im Profil landen."""
    current = run["report"].get("current") or {}
    parts = []
    for flag, key in (("-c", "ctx_size"), ("-ncmoe", "n_cpu_moe"), ("-t", "threads"), ("-ub", "ubatch"), ("--spec-type", "spec_type"), ("-ctk", "cache_k"), ("-ctv", "cache_v")):
        value = str(current.get(key, "")).strip()
        if value:
            parts.append(f"{flag} {value}")
    return tr("Gewählt: {values}", values="   ".join(parts)) if parts else ""
