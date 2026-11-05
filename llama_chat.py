"""Schlankes Chatfenster für llama-server. Es spricht dieselbe Schnittstelle wie die WebUI, braucht aber keinen Browser."""

import base64
import json
import mimetypes
import os
import queue
import re
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from llama_lang import tr
from llama_plot import format_number

BUDGETS = {"low": 512, "medium": 2048, "high": 8192, "max": -1}  # Dieselben Stufen wie in der WebUI.
EFFORT_LABELS = {"minimal": tr("Minimal"), "low": tr("Niedrig"), "medium": tr("Mittel"), "high": tr("Hoch"), "xhigh": tr("Sehr hoch"), "max": tr("Maximal")}
RENDER_INTERVAL = 0.1  # Beim Streamen wird die Antwort höchstens so oft neu gesetzt, in Sekunden.
MAX_TEXT_FILE = 2 * 1024 * 1024
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
DEFAULT_FONT_SIZE = 10
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24
THINK_TAGS = (("<think>", "</think>"), ("<|channel>thought", "<|channel|>"), ("<|think|>", "</|think|>"))
THINK_SPLIT = re.compile(r"""split\(\s*['"]</think>['"]\s*,\s*1\s*\)\s*\[\s*1\s*\]""")

PAPER = "#ffffff"
SOFT = "#f2f2f0"
INK = "#1a1a1a"
MUTED = "#6f6e69"
ACCENT = "#2a6fc4"
ERROR = "#b3261e"
METER_TRACK = "#e7e6e1"
METER_LEVELS = ((85, "#d03b3b"), (60, "#fab219"), (0, "#0ca30c"))  # Ab diesem Anteil in Prozent gilt die Farbe.
COMPACT_HINT = 70  # Ab diesem Füllstand in Prozent rät das Fenster zum Kompaktieren, solange noch Platz für die Zusammenfassung ist.
COMPACT_MIN_GAIN = 0.25  # Mindestens dieser Teil des Kontexts muss seit dem letzten Kompaktieren dazugekommen sein.
COMPACT_PROMPT = (tr("Fasse unser bisheriges Gespräch so zusammen, dass du es allein damit fortsetzen kannst. Übernimm Ziele, Fakten, Namen, Zahlen, Entscheidungen, Code und offene Punkte, soweit sie im Gespräch vorkommen. "
                  "Erfinde nichts und lass alles weg, was nicht vorkam, auch ganze Punkte. Schreibe knapp und in der Sprache des Gesprächs. Gib nur die Zusammenfassung aus, ohne Einleitung und ohne Rückfragen."))
SUMMARY_PREFIX = tr("Dieses Gespräch wurde kompaktiert. Zusammenfassung des bisherigen Verlaufs:")

INLINE = re.compile(
    r"`{1,3}([^`\n]+)`{1,3}"
    r"|\*\*(.+?)\*\*"
    r"|__(.+?)__"
    r"|~~(.+?)~~"
    r"|(?<![\w*])\*([^*\s](?:[^*\n]*[^*\s])?)\*(?![\w*])"
    r"|(?<![\w_])_([^_\s](?:[^_\n]*[^_\s])?)_(?![\w_])"
    r"|\[([^\]\n]+)\]\(([^)\s]+)\)"
    r"|\\\((.+?)\\\)"
    r"|(?<![\\\w$])\$(?=\S)([^$\n]+?)(?<=\S)\$(?![\d$])"  # Die Regeln um das Dollarzeichen lassen Preise wie „$5 und $10“ in Ruhe.
    r"|\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}"
)
MATH_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡",
    "sim": "∼", "propto": "∝", "ll": "≪", "gg": "≫", "infty": "∞", "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮", "partial": "∂", "nabla": "∇", "to": "→",
    "rightarrow": "→", "longrightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "implies": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔", "mapsto": "↦",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃", "emptyset": "∅", "neg": "¬",
    "land": "∧", "lor": "∨", "ldots": "…", "dots": "…", "cdots": "⋯", "circ": "∘", "degree": "°", "angle": "∠", "perp": "⊥", "parallel": "∥", "mid": "|",
    "lvert": "|", "rvert": "|", "langle": "⟨", "rangle": "⟩", "sin": "sin", "cos": "cos", "tan": "tan", "log": "log", "ln": "ln", "exp": "exp", "lim": "lim",
    "max": "max", "min": "min", "det": "det", "gcd": "ggT", "therefore": "∴", "hbar": "ℏ", "ell": "ℓ",
}
MATH_IGNORED = re.compile(r"\\(?:left\.|right\.|(?:left|right|big|Big|bigg|Bigg|displaystyle|textstyle|limits|nonumber|notag)\b)")
MATH_PLAIN_ARGS = {"text", "textbf", "textit", "textrm", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal", "operatorname", "boxed", "underline"}
MATH_ACCENTS = {"hat": "\u0302", "bar": "\u0304", "overline": "\u0305", "tilde": "\u0303", "vec": "\u20d7", "dot": "\u0307"}
MATH_SETS = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ"}
MATH_ATOM = re.compile(r"\d+(?:[.,]\d+)?[⁰¹²³⁴-⁹]*|[^\W\d_][̀-ͯ⃗]?[⁰¹²³⁴-⁹₀-₉]*")
SUPERSCRIPTS = dict(zip("0123456789+-=()ni,", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ,"))  # Für das Komma kennt Unicode kein eigenes Zeichen.
SUBSCRIPTS = dict(zip("0123456789+-=()aeoxnikmst,", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₙᵢₖₘₛₜ,"))
FENCE = re.compile(r"\s*(`{3,}|~{3,})\s*(?:([\w+.#-]+)(?=\s|$))?\s*(.*)")  # Zaun, Sprache, Code in derselben Zeile.
LATE_FENCE = re.compile(r"([^`]*[^`\s])\s*(`{3,}[\w+.#-]*)\s*$")  # Ein Codeblock, der am Ende einer Textzeile beginnt.
TABLE_RULE = re.compile(r"\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
LIST_ITEM = re.compile(r"(\s*)([-*+]|\d+[.)])\s+(.*)")
HEADING = re.compile(r"(#{1,6})\s+(.*)")
RULE = re.compile(r"\s*([-*_])(\s*\1){2,}\s*$")


def _script(content, table, mark):
    """Setzt Hoch- oder Tiefstellungen in echte Zeichen, soweit Unicode sie kennt."""
    content = content.strip()
    if content and all(char in table for char in content):
        return "".join(table[char] for char in content)
    return f"{mark}{content}" if len(content) == 1 else f"{mark}({content})"


def _grouped(content):
    """Klammert alles außer einer einzelnen Zahl oder Variable: 2a im Nenner muss als (2a) stehen, sonst ändert sich der Sinn."""
    content = content.strip()
    return content if MATH_ATOM.fullmatch(content) else f"({content})"


def _fraction(match):
    numerator = match.group(1).strip()
    if re.search(r"[\s+\-/±]", numerator):
        numerator = f"({numerator})"
    text = f"{numerator}/{_grouped(match.group(2))}"
    # Direkt angrenzende Glieder würden sonst in den Bruch rutschen: b/ax statt (b/a)x, 21/2 statt 2(1/2).
    before = match.string[match.start() - 1:match.start()] if match.start() else ""
    after = match.string[match.end():match.end() + 1]
    touching = before.isalnum() or after.isalnum() or (after and after in "(√^_")
    return f"({text})" if touching else text


def _math_argument(match):
    name, content = match.group(1), match.group(2)
    if name in MATH_PLAIN_ARGS:
        return content
    if name == "phantom":
        return ""
    if name == "sqrt":
        return f"√{_grouped(content)}"
    if name == "mathbb":
        return MATH_SETS.get(content, content)
    if name in MATH_ACCENTS:
        return content + MATH_ACCENTS[name] if len(content) == 1 else content
    return match.group(0)


def latex_to_text(source):
    """Macht eine LaTeX-Formel als Unicode-Text lesbar. Unbekannte Befehle bleiben stehen, statt still zu verschwinden."""
    text = source.replace("\\\\", "\n")
    # Geschützte Zeichen weichen Platzhaltern, damit die Regeln unten sie nicht als Klammer oder Tiefstellung lesen.
    literals = {"\\{": "\x01", "\\}": "\x02", "\\_": "\x03", "\\&": "\x04", "\\%": "%", "\\$": "$", "\\#": "#"}
    for raw, placeholder in literals.items():
        text = text.replace(raw, placeholder)
    text = MATH_IGNORED.sub("", text)
    text = re.sub(r"\\begin\{[^}]*\}(?:\{[^}]*\})?|\\end\{[^}]*\}", "\n", text)
    text = re.sub(r"\\hline\b", "\n" + "─" * 24 + "\n", text)
    text = re.sub(r"\\q?quad\b", "   ", text)
    text = re.sub(r"\\[,;: ]", " ", text).replace("\\!", "").replace("~", " ").replace("&", " ")
    text = re.sub(r"\^\s*\{?\s*\\circ\s*\}?", "°", text)
    text = re.sub(r"\\([a-zA-Z]+)", lambda match: MATH_SYMBOLS.get(match.group(1), match.group(0)), text)
    single_scripts = lambda value: re.sub(r"_([\w+-])", lambda m: _script(m.group(1), SUBSCRIPTS, "_"),
                                          re.sub(r"\^([\w+-])", lambda m: _script(m.group(1), SUPERSCRIPTS, "^"), value))
    text = single_scripts(text)
    while True:  # Von innen nach außen, bis keine Regel mehr greift.
        before = text
        text = re.sub(r"\^\{([^{}]*)\}", lambda m: _script(m.group(1), SUPERSCRIPTS, "^"), text)
        text = re.sub(r"_\{([^{}]*)\}", lambda m: _script(m.group(1), SUBSCRIPTS, "_"), text)
        text = re.sub(r"\\sqrt\[([^\]]*)\]\s*\{([^{}]*)\}", lambda m: f"{_script(m.group(1), SUPERSCRIPTS, '^')}√{_grouped(m.group(2))}", text)
        text = re.sub(r"\\binom\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"C(\1, \2)", text)
        text = re.sub(r"\\([a-zA-Z]+)\s*\{([^{}]*)\}", _math_argument, text)
        text = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", _fraction, text)
        if text == before:
            break
    text = single_scripts(text).replace("{", "").replace("}", "")
    for raw, placeholder in (("{", "\x01"), ("}", "\x02"), ("_", "\x03"), ("&", "\x04")):
        text = text.replace(placeholder, raw)
    return "\n".join(line for line in (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")) if line)


def inline_spans(text, tags=()):
    """Zerlegt eine Zeile in (Text, Tags, Aktion). Aktion ist None oder ("link", Adresse)."""
    spans, position = [], 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            spans.append((text[position:match.start()], tags, None))
        code, bold, bold_alt, strike, italic, italic_alt, label, url, *math = match.groups()
        if code is not None:
            spans.append((code, tags + ("code",), None))
        elif any(part is not None for part in math):
            formula = next(part for part in math if part is not None)
            spans.append((latex_to_text(formula).replace("\n", " "), tags + ("math",), None))
        elif bold or bold_alt:
            spans += inline_spans(bold or bold_alt, tags + ("bold",))
        elif strike:
            spans += inline_spans(strike, tags + ("strike",))
        elif italic or italic_alt:
            spans += inline_spans(italic or italic_alt, tags + ("italic",))
        else:
            spans.append((label, tags + ("link",), ("link", url)))
        position = match.end()
    if position < len(text):
        spans.append((text[position:], tags, None))
    return spans


def plain_inline(text):
    return "".join(span[0] for span in inline_spans(text))


def table_lines(rows):
    """Richtet eine Markdown-Tabelle für die Festbreitenschrift aus."""
    cells = [[plain_inline(cell.strip()) for cell in row.strip().strip("|").split("|")] for row in rows]
    columns = max(len(row) for row in cells)
    widths = [max(len(row[i]) if i < len(row) else 0 for row in cells) for i in range(columns)]
    lines = []
    for number, row in enumerate(cells):
        lines.append(" │ ".join((row[i] if i < len(row) else "").ljust(widths[i]) for i in range(columns)))
        if number == 0:
            lines.append("─┼─".join("─" * width for width in widths))
    return lines


def parse_markdown(text):
    """Wandelt Markdown in Abschnitte für das Textfeld. Auch unfertiger Text beim Streamen muss hier durchlaufen."""
    spans = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        late = LATE_FENCE.fullmatch(line)
        if late:
            lines[index:index + 1] = [late.group(1), late.group(2)]
            continue
        fence = FENCE.match(line)
        if fence:
            mark, first = fence.group(1), fence.group(3).rstrip()
            code_lines = [first] if first else []
            end = index
            if first.endswith(mark):  # Der Block endet in derselben Zeile.
                code_lines = [first[:-len(mark)]]
            else:
                end = index + 1
                while end < len(lines):
                    stripped = lines[end].strip()
                    if stripped.startswith(mark):
                        after = stripped[len(mark):].strip()
                        if after and not re.fullmatch(r"[\w+.#-]+", after):  # Text hinter dem Zaun darf nicht verschwinden.
                            lines.insert(end + 1, after)
                        break
                    if lines[end].rstrip().endswith(mark):  # Der Zaun klebt am Code.
                        code_lines.append(lines[end].rstrip()[:-len(mark)])
                        break
                    code_lines.append(lines[end])
                    end += 1
            code = "\n".join(code_lines)
            spans.append((f" {fence.group(2) or 'Code'}   ", ("codehead",), None))
            spans.append((tr("Kopieren"), ("codehead", "link"), ("copy", code)))
            spans.append(("\n", ("codehead",), None))
            spans.append((code + "\n", ("codeblock",), None))
            index = end + 1
            continue
        stripped = line.strip()
        if stripped.startswith("\\[") or stripped.startswith("$$"):
            closing = "\\]" if stripped.startswith("\\[") else "$$"
            first = stripped[2:]
            end = index
            while closing not in first and end + 1 < len(lines):  # Beim Streamen fehlt das Ende noch, dann gilt der Rest als Formel.
                end += 1
                first += "\n" + lines[end]
            formula, _, rest = first.partition(closing)
            spans.append((latex_to_text(formula) + "\n", ("mathblock",), None))
            if rest.strip():
                spans += inline_spans(rest.strip())
                spans.append(("\n", (), None))
            index = end + 1
            continue
        if line.lstrip().startswith("|") and index + 1 < len(lines) and TABLE_RULE.match(lines[index + 1]) and "-" in lines[index + 1]:
            end = index + 2
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            spans.append(("\n".join(table_lines([line] + lines[index + 2:end])) + "\n", ("table",), None))
            index = end
            continue
        heading = HEADING.match(line)
        item = LIST_ITEM.match(line)
        if heading:
            spans += inline_spans(heading.group(2), (f"h{min(len(heading.group(1)), 3)}",))
        elif RULE.match(line):
            spans.append(("─" * 40, ("rule",), None))
        elif item:
            level = min(len(item.group(1)) // 2, 2)
            bullet = "•" if item.group(2) in "-*+" else item.group(2)
            spans.append((f"{bullet} ", (f"list{level}",), None))
            spans += inline_spans(item.group(3), (f"list{level}",))
        elif line.startswith(">"):
            spans += inline_spans(line.lstrip("> "), ("quote",))
        else:
            spans += inline_spans(line)
        if index + 1 < len(lines):
            spans.append(("\n", (), None))
        index += 1
    return spans


def split_thinking(text):
    """Trennt Gedanken ab, die im Antworttext stehen. Das passiert, wenn der Server die Tags der Chatvorlage nicht kennt."""
    stripped = text.lstrip()
    for opening, closing in THINK_TAGS:
        if stripped.startswith(opening):
            thought, _, answer = stripped[len(opening):].partition(closing)
            return thought.strip(), answer.lstrip()
    return "", text


def reasoning_choices(effort_levels):
    """Auswahl für das Nachdenken: (Beschriftung, Stufe). Kennt die Chatvorlage eigene Stufen, gelten diese."""
    choices = [(tr("Standard"), "default"), (tr("Aus"), "off")]
    known = [level for level in effort_levels if level in EFFORT_LABELS]
    if known:
        return choices + [(EFFORT_LABELS[level], level) for level in known]
    for level, budget in BUDGETS.items():
        limit = tr("unbegrenzt") if budget < 0 else tr("max. {budget} Tokens", budget=format_number(budget, 0))
        choices.append((f"{EFFORT_LABELS[level]} ({limit})", level))
    return choices


def reasoning_fields(level, effort_levels=()):
    """Felder der Anfrage für eine Stufe. Sie folgen der WebUI, dazu kommt der zweite Name des Schalters."""
    fields = {"reasoning_format": "auto", "reasoning_control": True}
    if level == "default":
        return fields
    # Chatvorlagen nennen den Schalter verschieden: Qwen liest enable_thinking, Hermes 4 liest thinking.
    fields["chat_template_kwargs"] = {"enable_thinking": level != "off", "thinking": level != "off"}
    if level in effort_levels:
        fields["reasoning_effort"] = level
    elif BUDGETS.get(level, -1) >= 0:
        fields["thinking_budget_tokens"] = BUDGETS[level]
    return fields


def last_summary(messages):
    """Position der letzten Zusammenfassung im Chat oder -1."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == "summary":
            return index
    return -1


def needs_think_tag(props, level):
    """Hermes 4 nimmt beim Nachdenken von früheren Antworten nur, was hinter </think> steht. Ohne das Tag bliebe von ihnen nichts übrig."""
    return level not in ("default", "off") and bool(THINK_SPLIT.search(props.get("chat_template") or ""))


def api_messages(chat, end=None, plain=False, think_tag=False):
    """Baut aus einem gespeicherten Chat die Nachrichten für den Server. Was vor einer Zusammenfassung steht, geht nicht mehr mit.
    end begrenzt den Verlauf, plain lässt Bilder und Gedanken weg, think_tag setzt das Tag vor frühere Antworten (siehe needs_think_tag)."""
    stored = chat["messages"][:end]
    start = last_summary(stored)
    system = chat.get("system", "")
    if start >= 0:
        # Im System-Prompt stört die Zusammenfassung keine Chatvorlage, die strikt abwechselnde Rollen verlangt.
        system = f"{system}\n\n{SUMMARY_PREFIX}\n{stored[start]['content']}".strip()
    messages = [{"role": "system", "content": system}] if system else []
    for message in stored[start + 1:]:
        if message["role"] == "assistant":
            item = {"role": "assistant", "content": ("</think>" if think_tag else "") + message.get("content", "")}
            if message.get("reasoning") and not plain:
                item["reasoning_content"] = message["reasoning"]
            messages.append(item)
            continue
        text = message.get("content", "")
        images = []
        for attachment in message.get("attachments", []):
            if attachment["kind"] == "image" and plain:
                text += tr("\n\n[Bild {name}]", name=attachment['name'])
            elif attachment["kind"] == "image":
                images.append({"type": "image_url", "image_url": {"url": attachment["data"]}})
            else:
                text += tr("\n\nDatei {name}:\n```\n{text}\n```", name=attachment['name'], text=attachment['text'])
        messages.append({"role": "user", "content": images + [{"type": "text", "text": text}] if images else text})
    return messages


def stats_from_timings(timings):
    tokens = timings.get("predicted_n", 0)
    return {
        "tokens": tokens,
        "tps": timings.get("predicted_per_second", 0),
        "prompt_tokens": timings.get("prompt_n", 0),
        "prompt_tps": timings.get("prompt_per_second", 0),
        "used": timings.get("prompt_n", 0) + timings.get("cache_n", 0) + tokens,
    }


def stats_text(stats, context=None):
    parts = [tr("{tokens} Tokens", tokens=format_number(stats['tokens'], 0)), tr("{tps} Tok/s", tps=format_number(stats['tps']))]
    if stats.get("prompt_tokens"):
        parts.append(tr("Prompt {prompt_tokens} Tokens bei {prompt_tps} Tok/s", prompt_tokens=format_number(stats['prompt_tokens'], 0), prompt_tps=format_number(stats['prompt_tps'], 0)))
    if stats.get("used"):
        total = tr(" von {total}", total=format_number(context, 0)) if context else ""
        parts.append(tr("Kontext {used}{total}", used=format_number(stats['used'], 0), total=total))
    return "   ·   ".join(parts)


def fresh_messages(messages):
    """Was seit dem letzten Kompaktieren dazugekommen ist. Wörtlich behaltene Nachrichten zählen nicht, ihre Zahlen stammen von vorher."""
    start = last_summary(messages)
    return messages[start + 1 + messages[start].get("kept", 0):] if start >= 0 else messages


def context_used(messages):
    """Belegte Tokens laut der letzten Serverantwort im Chat, einschließlich der Antwort selbst."""
    for message in reversed(fresh_messages(messages)):
        if message.get("stats"):
            return message["stats"].get("used", 0)
    start = last_summary(messages)
    return messages[start].get("used", 0) if start >= 0 else 0


def can_compact(messages):
    return any(message["role"] == "assistant" for message in fresh_messages(messages))


def compact_worthwhile(messages, context):
    """Der Hinweis kommt erst, wenn der Kontext knapp wird und das Kompaktieren auch spürbar Platz schafft."""
    if not context or not can_compact(messages):
        return False
    used = context_used(messages)
    start = last_summary(messages)
    since = used - (messages[start].get("used", 0) if start >= 0 else 0)
    return 100 * used / context >= COMPACT_HINT and since >= context * COMPACT_MIN_GAIN


def compact_split(messages, context):
    """Bis zu dieser Stelle wird zusammengefasst. Das letzte Frage-Antwort-Paar bleibt wörtlich, solange es nicht selbst viel Kontext belegt."""
    start = last_summary(messages) + 1
    questions = [index for index in range(start, len(messages)) if messages[index]["role"] == "user"]
    if len(questions) < 2 or context_used(messages) - context_used(messages[:questions[-1]]) > context * COMPACT_MIN_GAIN:
        return len(messages)
    return questions[-1]


def meter_color(percent):
    return next(color for limit, color in METER_LEVELS if percent >= limit)


def meter_text(used, context):
    """Text unter dem Balken. Er nennt den Zustand auch in Worten, damit die Farbe nicht allein steht."""
    if not context:
        return tr("{used} Tokens, Kontextgröße unbekannt", used=format_number(used, 0)) if used else tr("Kontextgröße unbekannt")
    percent = 100 * used / context
    shown = tr("unter 1") if 0 < percent < 1 else int(percent)  # Abgerundet, damit 100 % erst bei wirklich vollem Kontext erscheint.
    text = tr("{used} von {context} Tokens ({shown} %)", used=format_number(used, 0), context=format_number(context, 0), shown=shown)
    if used >= context:
        return tr("{text}   ·   voll", text=text)
    text += tr("   ·   {value} frei", value=format_number(context - used, 0))
    return tr("{text}   ·   fast voll", text=text) if percent >= METER_LEVELS[0][0] else text


def http_post_json(url, body, api_key="", timeout=600):
    """Öffnet eine POST-Anfrage ohne System-Proxy. Der Aufrufer liest die Antwort."""
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)


def fetch_props(url, api_key=""):
    request = urllib.request.Request(f"{url}/props")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
        return json.load(response)


def server_error_text(error):
    """Übersetzt eine Fehlerantwort des Servers in einen brauchbaren Hinweis."""
    try:
        detail = json.loads(error.read().decode("utf-8", "replace")).get("error", {})
    except (OSError, ValueError, AttributeError):
        detail = {}
    if detail.get("type") == "exceed_context_size_error":
        return tr("Der Chat passt nicht mehr in den Kontext. Kompaktiere ihn, beginne einen neuen Chat oder starte den Server mit größerem Kontext.")
    return detail.get("message") or tr("Der Server meldet Fehler {code}.", code=error.code)


def stream_reply(url, api_key, body, cancel, emit):
    """Liest die gestreamte Antwort im Hintergrund und meldet alles über emit(Art, Wert)."""
    try:
        with http_post_json(f"{url}/v1/chat/completions", body, api_key) as response:
            for raw in response:
                if cancel.is_set():
                    emit("done", "stopped")
                    return
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("error:"):  # Tritt der Fehler erst im Stream auf, kommt er als eigene Zeile.
                    emit("error", json.loads(line[6:]).get("message", tr("Unbekannter Fehler des Servers.")))
                    return
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[5:])
                if chunk.get("error"):
                    emit("error", chunk["error"].get("message", tr("Unbekannter Fehler des Servers.")))
                    return
                if chunk.get("prompt_progress"):
                    emit("progress", chunk["prompt_progress"])
                if chunk.get("timings"):
                    emit("timings", chunk["timings"])
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        emit("reasoning", delta["reasoning_content"])
                    if delta.get("content"):
                        emit("content", delta["content"])
                    if choice.get("finish_reason"):
                        emit("finish", choice["finish_reason"])
        emit("done", "stopped" if cancel.is_set() else "")
    except urllib.error.HTTPError as err:
        emit("error", server_error_text(err))
    except (OSError, ValueError) as err:
        emit("error", "stopped" if cancel.is_set() else tr("Die Verbindung zum Server ist abgebrochen: {err}", err=err))


def count_tokens(url, api_key, messages):
    """Genaue Größe eines Prompts. Beide Anfragen rechnen nichts am Modell und belegen keinen Slot."""
    with http_post_json(f"{url}/apply-template", {"messages": messages}, api_key, timeout=30) as response:
        prompt = json.load(response)["prompt"]
    with http_post_json(f"{url}/tokenize", {"content": prompt, "add_special": True, "parse_special": True}, api_key, timeout=30) as response:
        return len(json.load(response)["tokens"])


def compact_worker(url, api_key, chat, split, context, used_before, cancel, emit):
    """Schreibt im Hintergrund die Zusammenfassung und meldet das Ergebnis mit emit. Der Chat selbst bleibt dabei unverändert."""
    try:
        messages = api_messages(chat, split, plain=True)
        first = 1 if messages and messages[0]["role"] == "system" else 0
        instruction = {"role": "user", "content": COMPACT_PROMPT}
        room = max(256, context // 8)
        dropped = 0
        shortened = False
        # Die Zusammenfassung braucht selbst Platz. Reicht er nicht, fallen zuerst die ältesten Nachrichten weg.
        # Passt dann eine einzelne Nachricht noch nicht, etwa eine Antwort bis an die Kontextgrenze, bleiben ihr Anfang und Ende.
        while (size := count_tokens(url, api_key, messages + [instruction])) + room > context:
            later = next((index for index in range(first + 1, len(messages)) if messages[index]["role"] == "user"), None)
            if later is not None:
                dropped += later - first
                del messages[first:later]
                continue
            longest = max(messages[first:], key=lambda message: len(message["content"]), default=None)
            keep = int(len(longest["content"]) * 0.8 * (context - room) / size) // 2 if longest else 0
            if keep < 50:
                emit("compact_end", {"error": tr("Schon der System-Prompt füllt den Kontext, so lässt sich der Chat nicht kürzen.")})
                return
            longest["content"] = tr("{content}\n[… gekürzt …]\n{content2}", content=longest['content'][:keep], content2=longest['content'][-keep:])
            shortened = True
        state = {"text": "", "finish": "", "error": ""}

        def collect(kind, value):
            if kind == "content":
                state["text"] += value
            elif kind == "timings" and value.get("predicted_n"):
                emit("compact_tokens", value["predicted_n"])
            elif kind == "finish":
                state["finish"] = value
            elif kind == "error":
                state["error"] = value

        body = {"messages": messages + [instruction], "stream": True, "sse_ping_interval": 1, "timings_per_token": True, "temperature": 0.2, **reasoning_fields("off")}  # Hier zählt Treue, nicht Abwechslung.
        stream_reply(url, api_key, body, cancel, collect)
        if cancel.is_set() or state["error"] == "stopped":
            emit("compact_end", {"stopped": True})
            return
        summary = split_thinking(state["text"])[1].strip()
        if state["error"]:
            emit("compact_end", {"error": state["error"]})
        elif state["finish"] == "length":
            emit("compact_end", {"error": tr("Die Zusammenfassung passte nicht mehr in den Kontext. Der Chat bleibt unverändert.")})
        elif not summary:
            emit("compact_end", {"error": tr("Das Modell hat keine Zusammenfassung geschrieben. Der Chat bleibt unverändert.")})
        else:
            notes = [tr("Die ältesten {dropped} Nachrichten passten nicht in den Kontext und fehlen in der Zusammenfassung.", dropped=dropped)] if dropped else []
            notes += [tr("Eine lange Nachricht ging nur gekürzt in die Zusammenfassung ein.")] if shortened else []
            entry = {"role": "summary", "content": summary, "kept": len(chat["messages"]) - split, "used_before": used_before, "note": " ".join(notes)}
            entry["used"] = count_tokens(url, api_key, api_messages({**chat, "messages": [entry] + chat["messages"][split:]}, plain=True))
            if entry["used"] >= used_before > 0:
                emit("compact_end", {"error": tr("Die Zusammenfassung wäre nicht kürzer als der Chat. Der Chat bleibt unverändert.")})
            else:
                emit("compact_end", {"entry": entry, "split": split})
    except urllib.error.HTTPError as err:
        emit("compact_end", {"error": server_error_text(err)})
    except (OSError, ValueError, KeyError) as err:
        emit("compact_end", {"error": tr("Die Verbindung zum Server ist abgebrochen: {err}", err=err)})


def load_store(path):
    """Liest die gespeicherten Chats. Eine kaputte Datei wird beiseitegelegt und nicht überschrieben."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data.get("chats"), list):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError):
        try:
            os.replace(path, f"{path}.defekt")
        except OSError:
            pass
    return {"chats": [], "reasoning": "default"}


def save_store(path, data):
    temporary = f"{path}.tmp"
    Path(temporary).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def read_attachment(path):
    """Liest eine Datei für den Chat. Wirft ValueError, wenn sie sich nicht anhängen lässt."""
    path = Path(path)
    if path.suffix.lower() in IMAGE_TYPES:
        kind = mimetypes.guess_type(path.name)[0] or "image/png"
        return {"kind": "image", "name": path.name, "data": f"data:{kind};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"}
    if path.stat().st_size > MAX_TEXT_FILE:
        raise ValueError(tr("{name} ist größer als 2 MB und würde den Kontext sprengen.", name=path.name))
    raw = path.read_bytes()
    if b"\0" in raw[:4096]:
        raise ValueError(tr("{name} ist keine Textdatei. Das Chatfenster liest nur Text und Bilder.", name=path.name))
    return {"kind": "text", "name": path.name, "text": raw.decode("utf-8", "replace")}


class ChatWindow(tk.Toplevel):
    def __init__(self, parent, store_path):
        super().__init__(parent)
        self.title(tr("Chat"))
        self.geometry("1040x760")
        self.minsize(640, 420)
        self.store_path = store_path
        self.store = load_store(store_path)
        self.url = ""
        self.api_key = ""
        self.props = {}
        self.events = queue.Queue()
        self.chat = None
        self.cancel = None
        self.stream_chat = None
        self.stream_message = None
        self.stream_text = ""
        self.stream_thought = ""
        self.stream_prompt = 0
        self.finish_reason = ""
        self.needs_render = False
        self.last_render = 0.0
        self.pending = []
        self.actions = {}
        self.message_tags = {}
        self.action_count = 0
        self.thought_state = {}
        self.choice_levels = []
        self._build()
        self._refresh_list()
        if self.store["chats"]:
            self.show_chat(self.store["chats"][-1])
        else:
            self.new_chat()
        self._update_controls()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._pump_id = self.after(50, self._pump)

    # ---------- Aufbau ----------

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        side = ttk.Frame(self, padding=8)
        side.grid(row=0, column=0, sticky="ns")
        side.rowconfigure(1, weight=1)
        ttk.Button(side, text=tr("Neuer Chat"), command=self.new_chat).grid(row=0, column=0, sticky="ew")
        self.chat_list = tk.Listbox(side, width=28, activestyle="none", exportselection=False, relief="flat", highlightthickness=0, bg=SOFT, fg=INK,
                                    selectbackground="#d9d8d3", selectforeground=INK, font=("Segoe UI", 9))
        self.chat_list.grid(row=1, column=0, sticky="ns", pady=6)
        self.chat_list.bind("<<ListboxSelect>>", lambda _: self._on_pick())
        ttk.Button(side, text=tr("Chat löschen"), command=self.delete_chat).grid(row=2, column=0, sticky="ew")

        main = ttk.Frame(self, padding=(0, 8, 8, 8))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        top = ttk.Frame(main)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        top.columnconfigure(0, weight=1)
        self.head_var = tk.StringVar()
        ttk.Label(top, textvariable=self.head_var, foreground=MUTED).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.meter = tk.Canvas(top, height=round(self.winfo_fpixels("6p")), bg=METER_TRACK, highlightthickness=0, borderwidth=0)
        self.meter.grid(row=1, column=0, sticky="ew")
        self.meter.bind("<Configure>", lambda _: self._draw_meter())
        meter_row = ttk.Frame(top)
        meter_row.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        self.compact_button = ttk.Button(meter_row, text=tr("Kompaktieren"), command=self.compact_chat)
        self.compact_button.pack(side="right")
        self.meter_var = tk.StringVar()
        ttk.Label(meter_row, textvariable=self.meter_var).pack(side="left")
        self.hint_var = tk.StringVar()
        ttk.Label(meter_row, textvariable=self.hint_var, foreground=ACCENT).pack(side="right", padx=(10, 8))

        # Die Trennlinie zwischen Chat und Eingabe lässt sich ziehen. Die Eingabe behält ihre Höhe, wenn das Fenster wächst.
        self.panes = tk.PanedWindow(main, orient="vertical", sashwidth=7, sashrelief="groove", sashcursor="sb_v_double_arrow", borderwidth=0,
                                    background=ttk.Style().lookup("TFrame", "background"))
        self.panes.grid(row=1, column=0, columnspan=2, sticky="nsew")
        chat_pane = ttk.Frame(self.panes)
        chat_pane.columnconfigure(0, weight=1)
        chat_pane.rowconfigure(0, weight=1)
        input_pane = ttk.Frame(self.panes)
        input_pane.columnconfigure(0, weight=1)
        input_pane.rowconfigure(1, weight=1)
        self.input_pane = input_pane

        self.text = tk.Text(chat_pane, wrap="word", state="disabled", relief="flat", bg=PAPER, fg=INK, font=("Segoe UI", 10), padx=18, pady=12, height=10,
                            spacing1=2, spacing3=2, highlightthickness=1, highlightbackground="#e3e2dd", cursor="arrow", takefocus=0)
        scroll = ttk.Scrollbar(chat_pane, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self._configure_tags()
        self.text.tag_bind("link", "<Enter>", lambda _: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda _: self.text.configure(cursor="arrow"))
        self.text.bind("<Button-1>", self._on_text_click)

        self.status_var = tk.StringVar()
        ttk.Label(chat_pane, textvariable=self.status_var, foreground=MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 2))
        self.attachment_var = tk.StringVar()
        self.attachment_label = ttk.Label(input_pane, textvariable=self.attachment_var, foreground=ACCENT, cursor="hand2")
        self.attachment_label.grid(row=0, column=0, columnspan=2, sticky="w")
        self.attachment_label.bind("<Button-1>", lambda _: self._clear_attachments())

        self.input = tk.Text(input_pane, height=self._input_lines(), wrap="word", relief="flat", bg=PAPER, fg=INK, font=("Segoe UI", self._font_size()), padx=10, pady=8, highlightthickness=1,
                             highlightbackground="#c9c8c2", highlightcolor=ACCENT, undo=True)
        input_scroll = ttk.Scrollbar(input_pane, command=self.input.yview)
        self.input.configure(yscrollcommand=input_scroll.set)
        self.input.grid(row=1, column=0, sticky="nsew", pady=(2, 6))
        input_scroll.grid(row=1, column=1, sticky="ns", pady=(2, 6))
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<Shift-Return>", lambda _: None)

        self.panes.add(chat_pane, stretch="always", minsize=round(self.winfo_fpixels("90p")))
        self.panes.add(input_pane, stretch="never", minsize=round(self.winfo_fpixels("50p")))
        saved = self.store.get("input_height")
        if isinstance(saved, int) and saved > 0:
            self.panes.paneconfigure(input_pane, height=min(saved, round(self.winfo_fpixels("360p"))))
        self.panes.bind("<ButtonRelease-1>", lambda _: self.after_idle(self._remember_input_height), add="+")

        bar = ttk.Frame(main)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(bar, text=tr("+ Datei"), command=self.add_attachment).pack(side="left")
        ttk.Button(bar, text=tr("System-Prompt"), command=self.edit_system).pack(side="left", padx=(6, 0))
        ttk.Label(bar, text=tr("Nachdenken")).pack(side="left", padx=(14, 4))
        self.reasoning_var = tk.StringVar()
        self.reasoning_box = ttk.Combobox(bar, textvariable=self.reasoning_var, state="readonly", width=26)
        self.reasoning_box.pack(side="left")
        self.reasoning_box.bind("<<ComboboxSelected>>", lambda _: self._on_reasoning())
        ttk.Label(bar, text=tr("Schrift")).pack(side="left", padx=(14, 4))
        ttk.Button(bar, text="−", width=2, command=lambda: self.change_font_size(-1)).pack(side="left")
        self.size_var = tk.StringVar(value=str(self._font_size()))
        ttk.Label(bar, textvariable=self.size_var, width=3, anchor="center").pack(side="left")
        ttk.Button(bar, text="+", width=2, command=lambda: self.change_font_size(1)).pack(side="left")
        for widget in (self.text, self.input):
            widget.bind("<Control-MouseWheel>", lambda event: self.change_font_size(1 if event.delta > 0 else -1))
        for sequence, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1), ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1), ("<Control-Key-0>", 0)):
            self.bind(sequence, lambda _event, change=step: self.change_font_size(change))
        self.send_button = ttk.Button(bar, text=tr("Senden"), command=self.toggle_send)
        self.send_button.pack(side="right")
        ttk.Label(bar, text=tr("Umschalt+Enter: neue Zeile"), foreground=MUTED).pack(side="right", padx=(0, 10))
        self._fill_reasoning()
        self.input.focus_set()

    def _remember_input_height(self):
        height = self.input_pane.winfo_height()
        if height > 1 and height != self.store.get("input_height"):
            self.store["input_height"] = height
            self._save()

    def _font_size(self):
        size = self.store.get("font_size", DEFAULT_FONT_SIZE)
        return size if isinstance(size, int) and MIN_FONT_SIZE <= size <= MAX_FONT_SIZE else DEFAULT_FONT_SIZE

    def _input_lines(self):
        """Bei großer Schrift hat das Eingabefeld weniger Zeilen, damit der Chat seinen Platz behält."""
        return max(2, round(40 / self._font_size()))

    def change_font_size(self, step):
        """Ändert die Schrift des Chats. step 0 stellt den Standard wieder her."""
        size = min(MAX_FONT_SIZE, max(MIN_FONT_SIZE, self._font_size() + step)) if step else DEFAULT_FONT_SIZE
        if size == self._font_size():
            return "break"
        follow = self._at_bottom()
        self.store["font_size"] = size
        self._configure_tags()
        self.input.configure(font=("Segoe UI", size), height=self._input_lines())
        self.size_var.set(str(size))
        if follow:
            self.text.update_idletasks()  # Erst nach dem neuen Umbruch steht fest, wo das Ende liegt.
            self.text.see("end")
        self._save()
        return "break"

    def _configure_tags(self):
        """Setzt alle Schriften relativ zur gewählten Größe. Lässt sich jederzeit erneut aufrufen, die Reihenfolge der Tags bleibt."""
        text = self.text
        size = self._font_size()
        ui = lambda delta=0, *style: ("Segoe UI", max(size + delta, 6), *style)
        mono = ("Consolas", size)
        text.configure(font=ui())
        text.tag_configure("role", foreground=MUTED, font=ui(-2))
        text.tag_configure("user", background=SOFT, lmargin1=140, lmargin2=140, rmargin=8, lmargincolor=PAPER, rmargincolor=PAPER)
        text.tag_configure("user_role", foreground=MUTED, font=ui(-2), lmargin1=140)
        text.tag_configure("user_footer", foreground=MUTED, font=ui(-2), lmargin1=140)
        text.tag_configure("attachment", foreground=ACCENT, font=ui(-1))
        text.tag_configure("thought", foreground=MUTED, font=ui(-1), lmargin1=14, lmargin2=14)
        text.tag_configure("toggle", foreground=MUTED, font=ui(-1))
        text.tag_configure("footer", foreground=MUTED, font=ui(-2))
        text.tag_configure("note", foreground=ERROR, font=ui(-1))
        text.tag_configure("bold", font=ui(0, "bold"))
        text.tag_configure("italic", font=ui(0, "italic"))
        text.tag_configure("strike", overstrike=True)
        text.tag_configure("h1", font=ui(5, "bold"), spacing1=8, spacing3=4)
        text.tag_configure("h2", font=ui(3, "bold"), spacing1=6, spacing3=3)
        text.tag_configure("h3", font=ui(1, "bold"), spacing1=4, spacing3=2)
        text.tag_configure("quote", foreground=MUTED, lmargin1=16, lmargin2=16)
        text.tag_configure("rule", foreground="#c9c8c2")
        for level in range(3):
            margin = 12 + 18 * level
            text.tag_configure(f"list{level}", lmargin1=margin, lmargin2=margin + 12)
        text.tag_configure("code", font=mono, background=SOFT)
        text.tag_configure("codeblock", font=mono, background=SOFT, lmargin1=10, lmargin2=10, rmargin=10, wrap="char")
        text.tag_configure("codehead", font=ui(-2), foreground=MUTED, background="#e7e6e1", lmargin1=10)
        text.tag_configure("table", font=mono, lmargin1=6, lmargin2=6, wrap="none")
        text.tag_configure("math", font=("Cambria", size + 1))
        text.tag_configure("mathblock", font=("Cambria", size + 2), lmargin1=24, lmargin2=24, spacing1=3, spacing3=3)
        text.tag_configure("summary", foreground=MUTED, font=ui(-1), justify="center")
        text.tag_configure("link", foreground=ACCENT, underline=True)
        text.tag_raise("link")

    def _fill_reasoning(self):
        efforts = (self.props.get("chat_template_caps") or {}).get("reasoning_effort_levels") or []
        choices = reasoning_choices(efforts)
        self.choice_levels = [level for _, level in choices]
        self.reasoning_box.configure(values=[label for label, _ in choices])
        level = self.store.get("reasoning", "default")
        self.reasoning_box.current(self.choice_levels.index(level) if level in self.choice_levels else 0)

    # ---------- Server ----------

    def set_server(self, url, api_key=""):
        """Der Starter meldet hier, ob und wo ein Server bereitsteht."""
        self.url = url or ""
        self.api_key = api_key
        self.props = {}
        if url:
            def read(address=url, key=api_key):
                try:
                    self.events.put(("props", fetch_props(address, key)))
                except (OSError, ValueError):
                    self.events.put(("props", {}))
            threading.Thread(target=read, daemon=True).start()
        self._update_controls()

    def _model_name(self):
        path = str(self.props.get("model_alias") or self.props.get("model_path") or "")
        return Path(path).stem if path else ""

    def _context(self):
        return (self.props.get("default_generation_settings") or {}).get("n_ctx")

    def _supports_images(self):
        return bool((self.props.get("modalities") or {}).get("vision"))

    def _update_controls(self):
        streaming = self.cancel is not None
        self.send_button.configure(text=tr("Stopp") if streaming else tr("Senden"), state="normal" if streaming or self.url else "disabled")
        if self.url:
            self.head_var.set(self._model_name() or tr("Modell"))
        else:
            self.head_var.set(tr("Kein Server aktiv. Starte ihn in NoProbLlama, die Chats bleiben hier lesbar."))
        self._draw_meter()

    def _messages(self):
        return self.chat["messages"] if self.chat else []

    def _context_used(self):
        messages = self._messages()
        if messages and messages[-1] is self.stream_message and self.stream_prompt:
            # Solange der Prompt eingelesen wird, zählen die Zeiten erst den fertigen Teil. Seine ganze Größe steht aber schon fest.
            return max(context_used(messages[-1:]), self.stream_prompt)
        return context_used(messages)

    def _draw_meter(self):
        used, context = self._context_used(), self._context()
        self.meter_var.set(meter_text(used, context))
        self.meter.delete("all")
        if used and context:
            percent = 100 * used / context
            width = self.meter.winfo_width()
            self.meter.create_rectangle(0, 0, max(2, width * min(percent, 100) / 100), self.meter.winfo_height(), fill=meter_color(percent), width=0)
        idle = self.cancel is None
        worthwhile = idle and self.url and compact_worthwhile(self._messages(), context)
        self.hint_var.set((tr("Kompaktieren macht wieder Platz") if used >= context else tr("Kompaktieren lohnt sich jetzt")) if worthwhile else "")
        self.compact_button.configure(state="normal" if idle and self.url and context and can_compact(self._messages()) else "disabled")

    # ---------- Chats ----------

    def _refresh_list(self):
        self.chat_list.delete(0, "end")
        for chat in reversed(self.store["chats"]):
            self.chat_list.insert("end", chat.get("title") or tr("Neuer Chat"))
        self._mark_current()

    def _mark_current(self):
        self.chat_list.selection_clear(0, "end")
        chats = self.store["chats"]
        if self.chat in chats:
            self.chat_list.selection_set(len(chats) - 1 - chats.index(self.chat))

    def _on_pick(self):
        picked = self.chat_list.curselection()
        if picked:
            self.show_chat(self.store["chats"][len(self.store["chats"]) - 1 - picked[0]])

    def new_chat(self):
        # Ein noch leerer Chat wird weiterverwendet, statt die Liste zu füllen.
        empty = next((chat for chat in self.store["chats"] if not chat["messages"]), None)
        chat = empty or {"id": uuid.uuid4().hex, "title": "", "created": time.strftime(tr("%d.%m.%Y %H:%M")), "system": (self.chat or {}).get("system", ""), "messages": []}
        if not empty:
            self.store["chats"].append(chat)
        self.show_chat(chat)
        self._refresh_list()
        self.input.focus_set()

    def delete_chat(self):
        chat = self.chat
        if not chat:
            return
        if chat["messages"] and not messagebox.askokcancel(tr("Chat löschen"), tr("„{value}“ wird endgültig gelöscht.", value=chat.get('title') or tr('Neuer Chat')), parent=self):
            return
        if chat is self.stream_chat and self.cancel:
            self.cancel.set()
            self.stream_chat = None
        self.store["chats"].remove(chat)
        self.chat = None
        self._save()
        if self.store["chats"]:
            self.show_chat(self.store["chats"][-1])
        else:
            self.new_chat()
        self._refresh_list()

    def show_chat(self, chat):
        self.chat = chat
        self.render_chat()
        self._mark_current()

    def _save(self):
        try:
            save_store(self.store_path, self.store)
        except OSError as err:
            self.status_var.set(tr("Chats nicht gespeichert: {err}", err=err))

    # ---------- Darstellung ----------

    def render_chat(self):
        text = self.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        for tags in self.message_tags.values():
            for tag in tags:
                text.tag_delete(tag)
                self.actions.pop(tag, None)
        self.message_tags = {}
        for mark in [m for m in text.mark_names() if m.startswith("msg")]:
            text.mark_unset(mark)
        for index in range(len(self.chat["messages"]) if self.chat else 0):
            self._append_slot(index)
            self._fill_message(index)
        text.configure(state="disabled")
        text.see("end")
        self._draw_meter()

    def _append_slot(self, index):
        """Legt die Marken für eine Nachricht an. Der Inhalt wird dazwischen gesetzt und kann jederzeit neu entstehen."""
        text = self.text
        text.insert("end-1c", "\n\n")
        text.mark_set(f"msg{index}.s", "end-3c")
        text.mark_gravity(f"msg{index}.s", "left")
        text.mark_set(f"msg{index}.e", "end-3c")
        text.mark_gravity(f"msg{index}.e", "right")

    def _action(self, index, callback):
        self.action_count += 1
        name = f"act{self.action_count}"
        self.actions[name] = callback
        self.message_tags.setdefault(index, []).append(name)
        return name

    def _fill_message(self, index):
        text = self.text
        message = self.chat["messages"][index]
        start, end = f"msg{index}.s", f"msg{index}.e"
        text.delete(start, end)
        for tag in self.message_tags.pop(index, []):
            text.tag_delete(tag)
            self.actions.pop(tag, None)

        def put(content, tags=(), callback=None):
            if callback:
                tags = tuple(tags) + (self._action(index, callback),)
            text.insert(end, content, tags)

        if message["role"] == "summary":
            is_open = self.thought_state.get(id(message), False)
            put(tr("───   Kompaktiert: Das Modell kennt alles darüber nur noch als Zusammenfassung   ───\n"), ("summary",))
            put(tr("{value} Zusammenfassung ({count} Zeichen)\n", value='▾' if is_open else '▸', count=format_number(len(message['content']), 0)), ("toggle", "link"), lambda: self._toggle_thought(index, not is_open))
            if is_open:
                put(message["content"].rstrip() + "\n", ("thought",))
            if message.get("note"):
                put(f"{message['note']}\n", ("note",))
            put(tr("Kontext {get} → {get2} Tokens   ·   ", get=format_number(message.get('used_before', 0), 0), get2=format_number(message.get('used', 0), 0)), ("footer",))
            put(tr("Kopieren"), ("footer", "link"), lambda: self._copy(message["content"]))
            if self.cancel is None and index == last_summary(self.chat["messages"]):
                put("   ·   ", ("footer",))
                put(tr("Rückgängig"), ("footer", "link"), lambda: self.undo_compact(index))
            return

        if message["role"] == "user":
            put(tr("Du\n"), ("user_role",))
            for attachment in message.get("attachments", []):
                label = tr("Bild") if attachment["kind"] == "image" else tr("Datei")
                put(f"[{label}] {attachment['name']}\n", ("attachment", "user"))
            put(message.get("content", "") or " ", ("user",))
            put("\n")
            put(tr("Kopieren"), ("user_footer", "link"), lambda: self._copy(message.get("content", "")))
            if self.cancel is None:
                put("   ·   ", ("user_footer",))
                put(tr("Bearbeiten"), ("user_footer", "link"), lambda: self.edit_message(index))
            return

        streaming = message is self.stream_message
        put(f"{message.get('model') or tr('Modell')}\n", ("role",))
        reasoning = message.get("reasoning", "")
        if reasoning:
            state = self.thought_state.get(id(message))
            is_open = state if state is not None else (streaming and not message.get("content"))
            arrow = "▾" if is_open else "▸"
            put(tr("{arrow} Gedanken ({count} Zeichen)\n", arrow=arrow, count=format_number(len(reasoning), 0)), ("toggle", "link"), lambda: self._toggle_thought(index, not is_open))
            if is_open:
                put(reasoning.rstrip() + "\n", ("thought",))
        for content, tags, action in parse_markdown(message.get("content", "")):
            callback = None
            if action and action[0] == "link":
                callback = lambda url=action[1]: webbrowser.open(url)
            elif action and action[0] == "copy":
                callback = lambda code=action[1]: self._copy(code)
            put(content, tags, callback)
        if message.get("note"):
            put(f"\n{message['note']}", ("note",))
        if streaming:
            return
        put("\n")
        if message.get("stats"):
            put(stats_text(message["stats"], self._context()) + "   ·   ", ("footer",))
        put(tr("Kopieren"), ("footer", "link"), lambda: self._copy(message.get("content", "")))
        if self.cancel is None and index == len(self.chat["messages"]) - 1:
            put("   ·   ", ("footer",))
            put(tr("Neu erzeugen"), ("footer", "link"), self.regenerate)

    def _at_bottom(self):
        # yview schätzt bei langen Absätzen nur, die Lage der letzten Zeile ist dagegen genau.
        return self.text.dlineinfo("end-1c") is not None

    def _refill(self, index):
        text = self.text
        follow = self._at_bottom()
        text.configure(state="normal")
        self._fill_message(index)
        text.configure(state="disabled")
        if follow:
            text.see("end")

    def _toggle_thought(self, index, state):
        self.thought_state[id(self.chat["messages"][index])] = state
        self._refill(index)

    def _on_text_click(self, event):
        self.text.focus_set()  # Sonst lässt sich markierter Text nicht mit Strg+C kopieren.
        for tag in self.text.tag_names(f"@{event.x},{event.y}"):
            callback = self.actions.get(tag)
            if callback:
                callback()
                return "break"
        return None

    def _copy(self, content):
        self.clipboard_clear()
        self.clipboard_append(content)
        self.status_var.set(tr("In die Zwischenablage kopiert."))

    # ---------- Eingabe ----------

    def _on_return(self, _event):
        if self.cancel is None:
            self.toggle_send()
        return "break"

    def _on_reasoning(self):
        picked = self.reasoning_box.current()
        if picked >= 0:
            self.store["reasoning"] = self.choice_levels[picked]
            self._save()

    def add_attachment(self):
        paths = filedialog.askopenfilenames(parent=self, title=tr("Dateien anhängen"))
        for path in paths:
            try:
                attachment = read_attachment(path)
                if attachment["kind"] == "image" and self.url and not self._supports_images():
                    raise ValueError(tr("Dieses Modell liest keine Bilder. Dafür braucht der Server ein passendes Modell mit --mmproj."))
                self.pending.append(attachment)
            except (OSError, ValueError) as err:
                messagebox.showerror(tr("Datei nicht angehängt"), str(err), parent=self)
        self._show_attachments()

    def _show_attachments(self):
        names = ", ".join(attachment["name"] for attachment in self.pending)
        self.attachment_var.set(tr("Angehängt: {names}   (zum Entfernen klicken)", names=names) if names else "")

    def _clear_attachments(self):
        self.pending = []
        self._show_attachments()

    def edit_system(self):
        chat = self.chat
        dialog = tk.Toplevel(self)
        dialog.title(tr("System-Prompt"))
        dialog.transient(self)
        dialog.geometry("560x320")
        ttk.Label(dialog, text=tr("Gilt für diesen Chat und für neue Chats, die du von hier aus beginnst."), foreground=MUTED).pack(anchor="w", padx=10, pady=(10, 4))
        field = tk.Text(dialog, wrap="word", font=("Segoe UI", 10), height=8)
        field.pack(fill="both", expand=True, padx=10)
        field.insert("1.0", chat.get("system", ""))

        def store():
            chat["system"] = field.get("1.0", "end-1c").strip()
            self._save()
            dialog.destroy()

        ttk.Button(dialog, text=tr("Übernehmen"), command=store).pack(anchor="e", padx=10, pady=10)
        field.focus_set()

    def toggle_send(self):
        if self.cancel:
            self.cancel.set()
            self.status_var.set(tr("Wird gestoppt…"))
            return
        content = self.input.get("1.0", "end-1c").strip()
        if not self.url or not (content or self.pending):
            return
        message = {"role": "user", "content": content, "attachments": self.pending}
        self.chat["messages"].append(message)
        if not self.chat.get("title"):
            self.chat["title"] = (content or self.pending[0]["name"]).splitlines()[0][:48]
            self._refresh_list()
        self.pending = []
        self._show_attachments()
        self.input.delete("1.0", "end")
        self._add_to_view(len(self.chat["messages"]) - 1)
        self._generate()

    def _add_to_view(self, index):
        text = self.text
        text.configure(state="normal")
        self._append_slot(index)
        self._fill_message(index)
        text.configure(state="disabled")
        text.see("end")

    def regenerate(self):
        if self.cancel is None and self.chat["messages"] and self.chat["messages"][-1]["role"] == "assistant":
            self.chat["messages"].pop()
            self.render_chat()
            self._generate()

    def edit_message(self, index):
        """Holt eine eigene Nachricht zurück ins Eingabefeld. Was danach kam, passt dann nicht mehr und fällt weg."""
        if self.cancel is not None:
            return
        messages = self.chat["messages"]
        later = len(messages) - index - 1
        if later > 1 and not messagebox.askokcancel(tr("Nachricht bearbeiten"), tr("Die {later} Nachrichten danach werden entfernt.", later=later), parent=self):
            return
        message = messages[index]
        del messages[index:]
        self.input.delete("1.0", "end")
        self.input.insert("1.0", message.get("content", ""))
        self.pending = list(message.get("attachments", []))
        self._show_attachments()
        self.render_chat()
        self._save()
        self.input.focus_set()

    # ---------- Kompaktieren ----------

    def compact_chat(self):
        context = self._context()
        if self.cancel is not None or not self.url or not context or not can_compact(self._messages()):
            return
        chat = self.chat
        self.stream_chat = chat  # Wie bei einer Antwort: Stopp bricht ab, Löschen des Chats ebenso, die Aktionen sind gesperrt.
        self.cancel = threading.Event()
        self.status_var.set(tr("Das Modell fasst den Chat zusammen…"))
        self.render_chat()
        self._update_controls()
        emit = lambda kind, value, inbox=self.events: inbox.put((kind, value))
        threading.Thread(target=compact_worker, args=(self.url, self.api_key, chat, compact_split(chat["messages"], context), context, self._context_used(), self.cancel, emit), daemon=True).start()

    def _end_compact(self, result):
        chat = self.stream_chat
        self.stream_chat = None
        self.cancel = None
        status = tr("Abgebrochen. Der Chat bleibt unverändert.") if result.get("stopped") else result.get("error", "")
        if chat is not None and result.get("entry"):
            chat["messages"].insert(result["split"], result["entry"])
            self._save()
            status = tr("Kompaktiert: {used_before} → {used} Tokens.", used_before=format_number(result['entry']['used_before'], 0), used=format_number(result['entry']['used'], 0))
        if chat is self.chat:
            self.render_chat()
        self.status_var.set(status)
        self._update_controls()
        if result.get("error"):
            messagebox.showerror(tr("Kompaktieren"), result["error"], parent=self)
        self.input.focus_set()

    def undo_compact(self, index):
        """Der alte Verlauf ist noch da, er geht dann einfach wieder vollständig an den Server."""
        if self.cancel is None and self.chat["messages"][index]["role"] == "summary":
            del self.chat["messages"][index]
            self.render_chat()
            self._save()
            self._update_controls()

    # ---------- Antwort ----------

    def _generate(self):
        chat = self.chat
        level = self.choice_levels[max(self.reasoning_box.current(), 0)]
        body = {"messages": api_messages(chat, think_tag=needs_think_tag(self.props, level)), "stream": True, "return_progress": True, "sse_ping_interval": 1, "timings_per_token": True}
        efforts = (self.props.get("chat_template_caps") or {}).get("reasoning_effort_levels") or []
        body.update(reasoning_fields(level, efforts))
        reply ={"role": "assistant", "content": "", "reasoning": "", "model": self._model_name()}
        chat["messages"].append(reply)
        self.stream_chat = chat
        self.stream_message = reply
        self.stream_text = ""
        self.stream_thought = ""
        self.stream_prompt = 0
        self.cancel = threading.Event()
        self.finish_reason = ""
        self.status_var.set(tr("Wartet auf das Modell…"))
        self.render_chat()  # Die Aktionen der älteren Nachrichten sind während der Antwort gesperrt.
        self._update_controls()
        emit = lambda kind, value, inbox=self.events: inbox.put((kind, value))
        threading.Thread(target=stream_reply, args=(self.url, self.api_key, body, self.cancel, emit), daemon=True).start()

    def _pump(self):
        self._pump_id = self.after(50, self._pump)
        try:
            while True:
                kind, value = self.events.get_nowait()
                self._on_event(kind, value)
        except queue.Empty:
            pass
        if self.needs_render and time.perf_counter() - self.last_render >= RENDER_INTERVAL:
            self._render_stream()

    def _render_stream(self):
        self.needs_render = False
        self.last_render = time.perf_counter()
        if self.stream_chat is self.chat and self.chat["messages"] and self.chat["messages"][-1] is self.stream_message:
            self._refill(len(self.chat["messages"]) - 1)

    def _on_event(self, kind, value):
        if kind == "props":
            self.props = value
            self._fill_reasoning()
            self._update_controls()
            return
        if kind == "compact_tokens":
            self.status_var.set(tr("Das Modell fasst den Chat zusammen: {value} Tokens…", value=format_number(value, 0)))
            return
        if kind == "compact_end":
            self._end_compact(value)
            return
        reply = self.stream_message
        if reply is None:
            return
        if kind == "reasoning":
            self.stream_thought += value
            reply["reasoning"] = self.stream_thought
            self.needs_render = True
        elif kind == "content":
            self.stream_text += value
            thought, reply["content"] = split_thinking(self.stream_text)
            reply["reasoning"] = self.stream_thought + thought
            self.needs_render = True
        elif kind == "progress":
            total = value.get("total") or 0
            self.stream_prompt = total
            self._draw_meter()
            if total and not reply["content"] and not reply["reasoning"]:
                self.status_var.set(tr("Prompt wird eingelesen: {value} %", value=100 * value.get('processed', 0) // total))
        elif kind == "timings":
            reply["stats"] = stats_from_timings(value)
            self._draw_meter()
            if reply["stats"]["tokens"]:
                self.status_var.set(stats_text(reply["stats"], self._context()))
        elif kind == "finish":
            self.finish_reason = value
        elif kind == "done":
            self._finish(value == "stopped", "")
        elif kind == "error":
            self._finish(value == "stopped", "" if value == "stopped" else value)

    def _finish(self, stopped, error):
        chat, reply = self.stream_chat, self.stream_message
        self.stream_chat = None
        self.stream_message = None
        self.cancel = None
        self.needs_render = False
        if chat is None or reply is None or chat not in self.store["chats"]:
            self._update_controls()
            self._save()
            return
        status = tr("Gestoppt.") if stopped else tr("Fertig.")
        if not reply["content"] and not reply["reasoning"]:
            # Ohne Antwort bleibt nichts im Verlauf. Der Text kommt zurück ins Eingabefeld, damit er nicht verloren geht.
            chat["messages"].pop()
            question = chat["messages"].pop() if chat["messages"] and chat["messages"][-1]["role"] == "user" else None
            if question and chat is self.chat and not self.input.get("1.0", "end-1c").strip():
                self.input.insert("1.0", question.get("content", ""))
                self.pending = list(question.get("attachments", []))
                self._show_attachments()
            if error:
                messagebox.showerror(tr("Chat"), error, parent=self)
            status = error or status
        elif error:
            reply["note"] = error
            status = error
        elif stopped:
            reply["note"] = tr("Gestoppt.")
        elif self.finish_reason == "length":
            reply["note"] = tr("Die Antwort ist abgeschnitten, weil der Kontext voll ist.")
            status = reply["note"]
        if reply.get("stats") and status == tr("Fertig."):
            status = stats_text(reply["stats"], self._context())
        self._save()
        if chat is self.chat:
            self.render_chat()
        self.status_var.set(status)
        self._update_controls()
        self.input.focus_set()

    def destroy(self):
        if self.cancel:
            self.cancel.set()
        self.after_cancel(self._pump_id)
        self._save()
        super().destroy()
