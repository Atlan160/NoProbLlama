"""NoProbLlama: Tkinter-Oberfläche zum Starten von llama-server."""

import http.client
import json
import os
import queue
import random
import re
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import llama_lang
import llama_plot
from llama_lang import tr
from llama_chat import ChatWindow, http_post_json
from llama_optimize import OptimizeWindow, repeat_lines
from llama_options import ALL_OPTS, DEFAULTS, TABS

CONFIG_PATH = llama_lang.CONFIG_PATH
ICON_PATH = Path(__file__).with_name("noprobllama.ico")
DEFAULT_LLAMA_DIR = r"C:\Program Files\LLama.cpp"
DEFAULT_MODELS_DIR = str(Path.home() / "Documents" / "KI Modelle")
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
OPENCODE_PROVIDER = "llamacpp"
OPENCODE_DESKTOP_CLI = (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "@opencodedesktop"
                        / "resources" / "opencode-cli.exe")

SPEEDTEST_MAX_TOKENS = 2000
REPEAT_TEST_TOKENS = 1500  # Länge des Texts, den das Modell für die Spekulations-Messung abschreibt.
PROMPT_TEST_TOKENS = 8000  # Ungefähr der Prompt von OpenCode. Das Tempo hängt von der Länge ab, deshalb immer gleich lang.
PROMPT_TEST_RESERVE = 256  # So viel Kontext bleibt beim Einlesen frei.
PROMPT_TEST_MIN_TOKENS = 1000  # Darunter wäre der Wert nicht aussagekräftig.
PROMPT_TEST_SECONDS = 10  # Länger wird nicht eingelesen. Bei überlaufendem VRAM bräuchte der ganze Prompt sonst über eine Minute pro Messpunkt.
PROMPT_IDLE_TIMEOUT = 60  # So lange darf der Server nach dem Abbruch noch am Prompt rechnen, bevor die Antwort gemessen wird.
SWEEP_ANSWER_SECONDS = 10  # Messzeit der Antwort in allen Tests, ab dem ersten Token. Danach wird abgeschnitten, das Tempo steht dann.
SWEEP_LOAD_TIMEOUT = 300  # Sekunden, die ein Messpunkt zum Laden des Modells bekommt.
SWEEP_GRID = (8448, 12288, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 196608, 262144)  # Ab 8448 passt der ganze Prompt.
SWEEP_GRID_DEFAULT_TOP = 65536
SWEEP_KEEP = 30  # So viele Tests bleiben im Verlauf.
SWEEP_FOLDER = "kontext_tests"  # Liegt neben der Konfigurationsdatei.
SWEEP_SETTLE_SECONDS = 2
PROMPT_TEST_WORDS = (
    "Eisenbahn Netz Strecke Bahnhof Signal Lokomotive Fahrplan Weiche Gleis Dampf Kohle Stadt Handel Brücke Tunnel Funktion "
    "Variable Rückgabewert Parameter Schleife Bedingung Datei Ordner Modul Klasse Objekt Liste Fehler Test Ergebnis"
).split()
SPEEDTEST_PROMPT = (
    "Schreibe einen ausführlichen, sachlichen Artikel über die Geschichte der Eisenbahn in Europa. "
    "Der Artikel soll mindestens 1500 Wörter lang sein und diese sechs Abschnitte behandeln: die ersten "
    "Dampflokomotiven in England, den Ausbau der Streckennetze im 19. Jahrhundert, die Folgen für Wirtschaft "
    "und Städte, die Elektrifizierung, den Wiederaufbau nach 1945 und den heutigen Hochgeschwindigkeitsverkehr. "
    "Schreibe ausschließlich Fließtext ohne Code, Tabellen oder Listen und beginne sofort mit dem Artikel."
)

BLOCK_TENSOR = re.compile(r"blk\.(\d+)\.")
ATTENTION_TENSOR = re.compile(r"blk\.\d+\.attn_k\b")  # Nur Layer mit diesem Tensor halten Kontext im KV-Cache.
EXPERT_TENSOR = re.compile(r"blk\.\d+\.ffn_(?:\w+_)?(?:ch)?exps\b")
PROJECTOR_ARCHITECTURES = ("clip", "mtmd")
FIT_RESERVE_MIB = 1024  # Gilt in llama.cpp, wenn das Feld --fit-target leer ist.
FIT_SMALL_RESERVE_MIB = 256  # Unsere Vorgabe. Darüber weist die VRAM-Prüfung auf das Feld hin.
BUFFER_RESERVE_MIB = 350  # Zusatz bei Experten im RAM, gemessen mit Qwen3.6-35B-A3B. Normale Modelle brauchen ihn nicht.
FIT_MIN_CONTEXT = 4096  # Standardwert von --fit-ctx
KV_TYPE_BITS = {"f32": 32, "f16": 16, "bf16": 16, "q8_0": 8, "q4_0": 4, "q4_1": 4, "iq4_nl": 4, "q5_0": 5, "q5_1": 5}
KV_FALLBACK_MIB_PER_TOKEN = 0.03  # Nur wenn die Datei keine Angaben zu den Attention-Köpfen enthält.
MTP_OVERHEAD_MIB = 450  # Gemessen mit Qwen3.8 IQ3 bei 8192 Kontext.
VRAM_TIGHT_MIB = 300  # So weit lag die Formel in den Messungen höchstens daneben, ohne die Ausreißer bei MoE.
VRAM_LEVEL_COLORS = {"ok": "#1f7a3d", "tight": "#b35900", "over": "#b3261e", "info": "#666666"}
VRAM_QUERY = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
    "$c = 'Win32_PerfFormattedData_GPUPerformanceCounters_GPU';"
    "$names = @{}; Get-Process | ForEach-Object { $names[[string]$_.Id] = $_.ProcessName };"
    "@{ adapters = @(Get-CimInstance ($c + 'AdapterMemory') | ForEach-Object {"
    "     @{ name = $_.Name; dedicated = [int64]$_.DedicatedUsage } });"
    "   processes = @(Get-CimInstance ($c + 'ProcessMemory') | Where-Object { $_.DedicatedUsage -gt 0 } | ForEach-Object {"
    "     @{ key = $_.Name; name = $names[($_.Name -split '_')[1]]; dedicated = [int64]$_.DedicatedUsage } });"
    "   gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })"
    "} | ConvertTo-Json -Depth 3 -Compress"
)
VRAM_KIND_ORDER = {"ai": 0, "app": 1, "own": 2, "system": 3}
VRAM_KIND_TEXT = {
    "ai": tr("Anderer KI-Server: beenden, wenn du ihn nicht brauchst"),
    "app": tr("Programm: Schließen gibt den Speicher frei"),
    "own": tr("Dein Server aus NoProbLlama"),
    "system": tr("Windows: bleibt belegt"),
}
VRAM_AI_PROGRAMS = {"llama-server", "ollama", "ollama app", "ollama_llama_server", "lm studio", "koboldcpp", "text-generation-webui"}
VRAM_SYSTEM_PROGRAMS = {
    "applicationframehost", "chxsmartscreen", "crossdeviceresume", "csrss", "ctfmon", "dwm", "explorer", "fontdrvhost",
    "lockapp", "runtimebroker", "searchhost", "securityhealthsystray", "shellexperiencehost", "shellhost", "sihost",
    "startmenuexperiencehost", "system", "systemsettings", "textinputhost", "winlogon",
}
MOE_KEYS = ("cpu_moe", "n_cpu_moe", "moe_cache")
CONTEXT_TABLE = (8192, 16384, 32768, 40000, 65536, 131072, 262144)
MTP_MAX_GPU_SHARE = 0.75  # Qwen3.8 Q4 (63 % im VRAM) gewann mit MTP, Qwen3.8 IQ3 (100 %) verlor.
METER_COLORS = ("#9a9a9a", "#7aa7d9", "#2f7ad1", "#0f4fa8")
METER_NAMES = (tr("keine Wirkung"), tr("geringe Wirkung"), tr("mittlere Wirkung"), tr("große Wirkung"))
METER_PADDING = (0, 12)


def meter_text(level):
    return "●" * level + "○" * (3 - level)


def fit_reserve_mib(value):
    """Liest die Reserve aus dem Feld --fit-target. Bei mehreren Geräten zählt das erste."""
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else FIT_RESERVE_MIB


def mtp_is_worthwhile(info, free_mib, reserve_mib=FIT_RESERVE_MIB):
    """MTP half in den Messungen nur, wenn das Modell großteils auf der CPU lief. Ohne bekannten VRAM rechnet alles dort."""
    if not info["mtp_layers"]:
        return False
    if free_mib is None:
        return True
    model_mib = info["always_mib"] + info["expert_mib"]
    return (free_mib - reserve_mib) / model_mib < MTP_MAX_GPU_SHARE


def resolve_spec_type(value, info, free_mib=None, reserve_mib=FIT_RESERVE_MIB):
    """Ersetzt auto durch den Typ, der sich für dieses Modell in den Messungen bewährt hat."""
    if value.strip() != "auto":
        return value
    if not info:
        return ""
    use_mtp = mtp_is_worthwhile(info, free_mib, reserve_mib)
    if info["experts"]:  # N-Gram bremst MoE-Modelle, weil große Batches viele Experten berühren.
        return "draft-mtp" if use_mtp else "none"
    if info["mtp_layers"]:
        return "draft-mtp,ngram-mod" if use_mtp else "ngram-mod"
    return "ngram-simple"


def split_extra(text):
    """Zerlegt Zusatzargumente; Backslashes in Windows-Pfaden bleiben erhalten."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    return list(lexer)


def build_args(exe, model, values):
    """Baut die Kommandozeile. Leere Felder werden weggelassen."""
    args = [str(exe)]
    if model:
        args += ["-m", str(model)]
    for opt in ALL_OPTS:
        val = values.get(opt.key, "").strip()
        if not val or opt.key == "extra":
            continue
        if opt.kind == "flag":
            args.append(opt.flag)
        elif opt.kind == "toggle":
            args.append(opt.flag if val == "an" else opt.off_flag)
        else:
            args += [opt.flag, val]
    extra = values.get("extra", "").strip()
    if extra:
        args += split_extra(extra)
    return args


def validate(values):
    """Liefert Fehlermeldungen für ungültige Felder."""
    errors = []
    for opt in ALL_OPTS:
        val = values.get(opt.key, "").strip()
        if not val:
            continue
        if opt.pattern and not re.fullmatch(opt.pattern, val):
            errors.append(tr("{label} ({flag}): ungültiger Wert „{val}“", label=opt.label, flag=opt.flag, val=val))
        if opt.kind == "file" and not Path(val).is_file():
            errors.append(tr("{label} ({flag}): Datei nicht gefunden", label=opt.label, flag=opt.flag))
    try:
        split_extra(values.get("extra", ""))
    except ValueError:
        errors.append(tr("Weitere Argumente: Anführungszeichen nicht geschlossen"))
    return errors


class GgufReader:
    """Liest einen GGUF-Header und prüft jede Länge, damit beschädigte Dateien eine klare Fehlermeldung ergeben."""

    SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    MAX_LIST = 4096  # Listen pro Layer sind kurz. Längere wie das Vokabular werden übersprungen.

    def __init__(self, file, size):
        self.file = file
        self.size = size

    def read(self, fmt):
        size = struct.calcsize(fmt)
        data = self.file.read(size)
        if len(data) < size:
            raise ValueError(tr("Die Datei ist beschädigt oder unvollständig."))
        return struct.unpack(fmt, data)[0]

    def count(self, fmt="<Q"):
        value = self.read(fmt)
        if value > self.size:
            raise ValueError(tr("Die Datei ist beschädigt oder unvollständig."))
        return value

    def text(self):
        return self.file.read(self.count()).decode("utf-8", "replace")

    def value(self, kind):
        if kind in self.SCALARS:
            return self.read(self.SCALARS[kind])
        if kind == 8:
            return self.text()
        if kind != 9:
            raise ValueError(tr("Unbekannter GGUF-Datentyp {kind}.", kind=kind))
        item_kind, items = self.read("<I"), self.count()
        if item_kind in self.SCALARS:
            item_format = self.SCALARS[item_kind]
            if items <= self.MAX_LIST:
                return [self.read(item_format) for _ in range(items)]
            self.file.seek(items * struct.calcsize(item_format), 1)
            return None
        for _ in range(items):
            self.value(item_kind)
        return None

    def header(self):
        """Liefert (Metadaten, Anzahl Tensoren)."""
        if self.file.read(4) != b"GGUF":
            raise ValueError(tr("Keine GGUF-Datei."))
        version = self.read("<I")
        if version not in (2, 3):
            raise ValueError(tr("GGUF-Version {version} wird nicht unterstützt.", version=version))
        tensor_count, key_count = self.count(), self.count()
        meta = {}
        for _ in range(key_count):
            key = self.text()
            meta[key] = self.value(self.read("<I"))
        return meta, tensor_count

    def tensors(self, tensor_count):
        """Liefert die Tensoren als sortierte Liste aus (Position, Name)."""
        found = []
        for _ in range(tensor_count):
            name = self.text()
            dimensions = self.read("<I")
            if dimensions > 16:
                raise ValueError(tr("Die Datei ist beschädigt oder unvollständig."))
            self.file.seek(8 * dimensions + 4, 1)
            found.append((self.read("<Q"), name))
        return sorted(found)


def model_value(meta, arch, suffix):
    """Sucht einen Wert unter der Architektur. Fehlt sie, zählt jeder passende Schlüssel außer denen der Bildmodelle."""
    value = meta.get(f"{arch}.{suffix}") if arch else None
    if value is None and not arch:
        for key, candidate in meta.items():
            if key.endswith(f".{suffix}") and ".vision." not in key and ".audio." not in key:
                return candidate
    return value


def whole_number(value):
    """Akzeptiert nur echte Zahlen, damit ungewöhnliche Metadaten keine Rechnung stören."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def per_layer(value, layer):
    """Metadaten können für alle Layer gleich oder als Liste pro Layer vorliegen."""
    if isinstance(value, list):
        return whole_number(value[layer]) if layer < len(value) else 0
    return whole_number(value)


def kv_layer_units(meta, arch, blocks, attention_layers):
    """Liefert pro Layer mit Kontext (Layer, K-Werte, V-Werte) je Token. Fehlen Angaben, bleibt die Liste leer."""
    embedding = whole_number(model_value(meta, arch, "embedding_length"))
    heads = model_value(meta, arch, "attention.head_count")
    heads_kv = model_value(meta, arch, "attention.head_count_kv")
    key_length = model_value(meta, arch, "attention.key_length")
    value_length = model_value(meta, arch, "attention.value_length")
    recurrent = model_value(meta, arch, "attention.recurrent_layers")
    layers = sorted(attention_layers) or range(blocks)
    units = []
    for layer in layers:
        # Layer ohne attn_k-Tensor halten keinen Kontext. Ohne solche Tensoren zählt die Liste der Modell-Datei.
        if not attention_layers and isinstance(recurrent, list) and layer < len(recurrent) and recurrent[layer]:
            continue
        layer_heads = per_layer(heads, layer)
        layer_heads_kv = per_layer(heads_kv, layer) if heads_kv is not None else layer_heads
        if not layer_heads_kv:
            continue
        key = per_layer(key_length, layer) or (embedding // layer_heads if layer_heads else 0)
        value = per_layer(value_length, layer) or (embedding // layer_heads if layer_heads else 0)
        if key and value:
            units.append((layer, key * layer_heads_kv, value * layer_heads_kv))
    return units


def read_gguf_info(path):
    """Liest alles für die Einstellungen aus dem Header, ohne Tensordaten anzufassen. Tensornamen haben Vorrang vor Metadaten."""
    path = Path(path)
    parts = model_parts(path)
    meta = {}
    expert_layers = {}
    attention_layers = set()
    highest_block = -1
    has_mtp_tensors = False
    total_bytes = 0
    for part in parts:
        size = part.stat().st_size
        with open(part, "rb") as file:
            reader = GgufReader(file, size)
            part_meta, tensor_count = reader.header()
            meta = meta or part_meta
            tensors = reader.tensors(tensor_count)
            alignment = whole_number(part_meta.get("general.alignment")) or 32
            data_size = size - (file.tell() + alignment - 1) // alignment * alignment
        if data_size < 0 or (tensors and tensors[-1][0] > data_size):
            raise ValueError(tr("Die Datei ist unvollständig. Lade sie bitte erneut herunter."))
        total_bytes += data_size
        # Die Größe eines Tensors ergibt sich aus dem Abstand zum nächsten.
        for index, (offset, name) in enumerate(tensors):
            block = BLOCK_TENSOR.match(name)
            if block:
                highest_block = max(highest_block, int(block.group(1)))
            if ATTENTION_TENSOR.match(name):
                attention_layers.add(int(block.group(1)))
            if "mtp" in name.lower():
                has_mtp_tensors = True
            if EXPERT_TENSOR.match(name):
                end = tensors[index + 1][0] if index + 1 < len(tensors) else data_size
                layer = int(block.group(1))
                expert_layers[layer] = expert_layers.get(layer, 0) + end - offset
    split = re.search(r"-\d+-of-(\d+)\.gguf$", path.name)
    arch = meta.get("general.architecture") if isinstance(meta.get("general.architecture"), str) else ""
    value = lambda suffix: model_value(meta, arch, suffix)
    blocks = whole_number(value("block_count")) or highest_block + 1
    # Die MTP-Angabe zählt nur, wenn die Layer dazu auch in der Datei liegen.
    next_layers = whole_number(value("nextn_predict_layers"))
    mtp_layers = next_layers if next_layers and highest_block + 1 >= blocks else 0
    expert_count = whole_number(value("expert_count"))
    template = meta.get("tokenizer.chat_template")
    expert_bytes = sum(expert_layers.values())
    units = kv_layer_units(meta, arch, blocks, attention_layers)
    return {
        "blocks": blocks,
        "kv_layers": units,
        "kv_exact": bool(units) and value("attention.sliding_window") is None,
        "expert_layers": len(expert_layers),
        "expert_mib": expert_bytes / 1024 ** 2,
        "always_mib": (total_bytes - expert_bytes) / 1024 ** 2,
        "name": meta.get("general.name") if isinstance(meta.get("general.name"), str) and meta.get("general.name") else path.stem,
        "arch": arch,
        "size_label": meta.get("general.size_label") if isinstance(meta.get("general.size_label"), str) else "",
        "context": whole_number(value("context_length")) or None,
        "mtp_layers": mtp_layers or (1 if has_mtp_tensors else 0),
        "experts": expert_count or (1 if expert_layers else 0),
        "experts_used": whole_number(value("expert_used_count")) if expert_count else 0,
        "tools": isinstance(template, str) and "tools" in template,
        "missing_parts": max(0, int(split.group(1)) - len(parts)) if split else 0,
    }


def gguf_architecture(path):
    """Liest nur den Anfang der Datei. Unlesbare Dateien gelten als Modell, die Fehlermeldung kommt beim Auswählen."""
    try:
        with open(path, "rb") as file:
            reader = GgufReader(file, path.stat().st_size)
            if file.read(4) != b"GGUF" or reader.read("<I") not in (2, 3):
                return ""
            reader.count()
            for _ in range(min(reader.count(), 16)):
                key = reader.text()
                kind = reader.read("<I")
                if kind == 9:
                    return ""
                item = reader.value(kind)
                if key == "general.architecture":
                    return item
    except (OSError, ValueError, struct.error):
        pass
    return ""


def scan_models(folder):
    """Liefert (Modelle, mmproj-Dateien). Von geteilten Modellen nur den ersten Teil."""
    models, mmprojs = [], []
    root = Path(folder)
    if not root.is_dir():
        return models, mmprojs
    for path in sorted(root.rglob("*.gguf"), key=lambda p: p.name.lower()):
        if re.search(r"-(?!0*1-)\d+-of-\d+\.gguf$", path.name):
            continue
        is_projector = "mmproj" in path.name.lower() or gguf_architecture(path) in PROJECTOR_ARCHITECTURES
        (mmprojs if is_projector else models).append(path)
    return models, mmprojs


def model_parts(path):
    """Alle vorhandenen Dateien eines Modells, bei geteilten Modellen in Reihenfolge."""
    match = re.search(r"-\d+-of-(\d+)\.gguf$", path.name)
    if not match:
        return [path]
    return sorted(path.parent.glob(path.name[:match.start()] + f"-*-of-{match.group(1)}.gguf")) or [path]


def model_size_gib(path):
    return sum(p.stat().st_size for p in model_parts(path)) / 1024 ** 3


def kv_estimate_mib(info, context, cache_k="", cache_v="", gpu_layers=None):
    """Größe des KV-Caches im VRAM: Tokens x K- und V-Werte der Layer auf der GPU in ihrem Datentyp."""
    if not info["kv_layers"]:
        return KV_FALLBACK_MIB_PER_TOKEN * context  # Diese Angaben fehlen in der Datei, also nur ein grober Wert.
    key_bits = KV_TYPE_BITS.get(cache_k, 16)
    value_bits = KV_TYPE_BITS.get(cache_v, 16)
    bits = sum(key * key_bits + value * value_bits for layer, key, value in info["kv_layers"]
               if gpu_layers is None or layer < gpu_layers)
    return context * bits / 8 / 1024 ** 2


def estimate_vram(info, values, free_mib, spec_type=""):
    """Schätzt den VRAM-Bedarf aus Modell und Einstellungen und ob er in den freien Speicher passt."""
    total = info["always_mib"] + info["expert_mib"]
    ngl = values.get("ngl", "").strip()
    no_gpu = ngl == "0" or values.get("device", "").strip() == "none"
    weights, buffer = total, 0
    n_cpu_moe = values.get("n_cpu_moe", "").strip()
    if info["expert_layers"] and values.get("cpu_moe"):
        weights, buffer = info["always_mib"], BUFFER_RESERVE_MIB
    elif info["expert_layers"] and n_cpu_moe.isdigit():
        on_gpu = max(0, info["expert_layers"] - int(n_cpu_moe))
        weights = info["always_mib"] + info["expert_mib"] * on_gpu / info["expert_layers"]
        buffer = BUFFER_RESERVE_MIB
    elif ngl.isdigit() and info["blocks"] and int(ngl) < info["blocks"]:
        weights = total * int(ngl) / info["blocks"]
    extra = MTP_OVERHEAD_MIB if "draft-mtp" in spec_type else 0
    for key in ("mmproj", "draft_model"):
        try:
            extra += Path(values.get(key, "").strip()).stat().st_size / 1024 ** 2 if values.get(key, "").strip() else 0
        except OSError:
            pass
    fit_off = values.get("fit", "").strip() == "off"
    usable = None if free_mib is None else free_mib - (0 if fit_off else fit_reserve_mib(values.get("fit_target", "")))
    cache_k, cache_v = values.get("cache_k", "").strip(), values.get("cache_v", "").strip()
    kv_on_gpu = values.get("kv_offload", "").strip() != "aus"
    # Der Kontext liegt bei dem Gerät seines Layers. Layer auf der CPU belegen keinen VRAM.
    gpu_layers = int(ngl) if ngl.isdigit() else None
    per_token = kv_estimate_mib(info, 1, cache_k, cache_v, gpu_layers) if kv_on_gpu else 0
    trained = info["context"] or 0
    max_context = None
    if usable is not None and per_token:
        room = usable - weights - buffer - extra
        max_context = min(int(room / per_token) // 1024 * 1024, trained or 2 ** 31) if room > 0 else 0
    ctx = values.get("ctx_size", "").strip()
    explicit = ctx.isdigit() and int(ctx) > 0
    # Ohne festen Wert wählt --fit den Kontext selbst, höchstens bis zum Mindestkontext herab. Sonst gilt der Wert aus dem Modell.
    auto_context = not explicit and not fit_off and usable is not None
    if explicit:
        context = int(ctx)
    elif auto_context:
        fit_ctx = values.get("fit_ctx", "").strip()
        context = max(max_context or 0, int(fit_ctx) if fit_ctx.isdigit() else FIT_MIN_CONTEXT)
    else:
        context = trained or 32768
    kv = per_token * context
    needed = weights + kv + buffer + extra
    over = 0 if usable is None else max(0, needed - usable)
    return {
        "no_gpu": no_gpu,
        "weights": weights,
        "kv": kv,
        "buffer": buffer,
        "extra": extra,
        "needed": needed,
        "free": free_mib,
        "usable": usable,
        "context": context,
        "auto_context": auto_context,
        "max_context": max_context,
        "over": over,
        "over_share": min(1, over / weights) if weights else 0,
        "fit_blocked": bool(values.get("cpu_moe") or n_cpu_moe or values.get("override_tensor", "").strip()
                            or ngl not in ("", "auto") or fit_off),
        "kv_exact": info["kv_exact"],
    }


def format_mib(value):
    return llama_plot.format_number(value, 0)


def vram_estimate_text(est):
    """Liefert (Text, Stufe) für die Anzeige. Stufe: ok, tight, over oder info."""
    if est["no_gpu"]:
        return tr("VRAM-Schätzung: Das Modell läuft auf der CPU und braucht keinen VRAM."), "info"
    rough = "" if est["kv_exact"] else tr(" (grob, die Datei nennt nicht alle Werte)")
    parts = [tr("Modell {weights}", weights=format_mib(est['weights'])), tr("Kontext {kv}{rough}", kv=format_mib(est['kv']), rough=rough)]
    if est["buffer"] or est["extra"]:
        parts.append(tr("Zusatz {value}", value=format_mib(est['buffer'] + est['extra'])))
    head = tr("VRAM-Schätzung: {join} = {needed} MiB", join=' + '.join(parts), needed=format_mib(est['needed']))
    if est["auto_context"]:
        head += tr("   (--fit wählt etwa {context} Tokens Kontext)", context=format_mib(est['context']))
    if est["usable"] is None:
        return head + tr(". Der freie VRAM ist nicht bekannt."), "info"
    head += tr(", nutzbar {usable} MiB", usable=format_mib(est['usable']))
    limit = ""
    if est["max_context"] and not est["auto_context"]:
        limit = tr(" Alles passt bis etwa {max_context} Tokens Kontext.", max_context=format_mib(est['max_context']))
    if not est["over"]:
        spare = est["usable"] - est["needed"]
        if spare < VRAM_TIGHT_MIB:
            return tr("{head}\nPasst knapp, die Schätzung kann bis zu {vram_tight_mib} MiB daneben liegen.{limit}", head=head, vram_tight_mib=VRAM_TIGHT_MIB, limit=limit), "tight"
        return tr("{head}\nPasst, etwa {spare} MiB bleiben frei.{limit}", head=head, spare=format_mib(spare), limit=limit), "ok"
    if est["fit_blocked"]:
        effect = tr("Der VRAM läuft über, das Tempo bricht dann stark ein.")
    else:
        effect = tr("--fit lagert etwa {value:.0f} % des Modells in den RAM aus, das bremst deutlich.", value=est['over_share'] * 100)
    if not est["max_context"]:
        limit = tr(" Das Modell passt auch mit sehr kleinem Kontext nicht ganz in den VRAM.")
    return tr("{head}\n{over} MiB zu viel. {effect}{limit}", head=head, over=format_mib(est['over']), effect=effect, limit=limit), "over"


def moe_suggestion(info, free_mib, context, reserve_mib=FIT_RESERVE_MIB, cache_k="", cache_v=""):
    """Schätzt, wie viele Expertenschichten in den freien VRAM passen. Ohne Experten: None."""
    if not info.get("expert_layers"):
        return None
    layers = info["expert_layers"]
    layer_mib = info["expert_mib"] / layers
    needed = info["always_mib"] + kv_estimate_mib(info, context, cache_k, cache_v) + BUFFER_RESERVE_MIB
    budget = free_mib - reserve_mib - needed
    on_gpu = min(layers, max(0, int(budget // layer_mib)))
    return {
        "n_cpu_moe": layers - on_gpu,
        "on_gpu": on_gpu,
        "layers": layers,
        "layer_mib": layer_mib,
        "needed_mib": needed,
        "free_mib": free_mib,
        "reserve_mib": reserve_mib,
        "expert_mib": info["expert_mib"],
    }


def client_host(host):
    """Adresse, unter der dieser PC den Server erreicht."""
    first = (host or "127.0.0.1").split(",")[0].strip()
    return "127.0.0.1" if first in ("0.0.0.0", "::", "") else first


def merge_opencode_config(config, base_url, model_id, name, context, api_key=""):
    """Trägt den Server als Provider ein und lässt alle anderen Einträge unverändert."""
    config = dict(config)
    config.setdefault("$schema", "https://opencode.ai/config.json")
    providers = config["provider"] = dict(config.get("provider", {}))
    provider = providers[OPENCODE_PROVIDER] = dict(providers.get(OPENCODE_PROVIDER, {}))
    provider["npm"] = "@ai-sdk/openai-compatible"
    provider["name"] = tr("llama.cpp (lokal)")
    options = provider["options"] = dict(provider.get("options", {}))
    options["baseURL"] = base_url
    if api_key:
        options["apiKey"] = api_key
    else:
        options.pop("apiKey", None)
    models = provider["models"] = dict(provider.get("models", {}))
    models[model_id] = {
        "name": name,
        "limit": {"context": context, "output": min(8192, context // 4)},
    }
    # Ein selbst gewähltes Modell eines anderen Anbieters bleibt die Vorgabe.
    if config.get("model", f"{OPENCODE_PROVIDER}/").startswith(f"{OPENCODE_PROVIDER}/"):
        config["model"] = f"{OPENCODE_PROVIDER}/{model_id}"
    return config


def speedtest_result(timings, first_token, total, finish_reason):
    """Fasst die Messwerte des Servers für die Ergebnisliste zusammen."""
    if not timings.get("predicted_n"):
        raise ValueError(tr("Der Server hat keine Messwerte geliefert."))
    return {
        "tokens": timings["predicted_n"],
        "tps": timings.get("predicted_per_second", 0),
        "prompt_tokens": timings.get("prompt_n", 0),
        "prompt_tps": timings.get("prompt_per_second", 0),
        "first_token": first_token,
        "total": total,
        "draft_n": timings.get("draft_n", 0),
        "draft_accepted": timings.get("draft_n_accepted", 0),
        "finish": finish_reason,
    }


def stream_chat(url, api_key, body, cancel, on_token=None, on_progress=None, time_limit=None):
    """Liest eine gestreamte Antwort. Liefert (Zeiten, Sekunden bis Token 1, Gesamtdauer, Endegrund), bei Abbruch None.

    time_limit schneidet die Antwort so viele Sekunden nach Token 1 ab. Dafür muss body "timings_per_token" setzen,
    denn sonst meldet der Server seine Zeiten erst am Ende der Antwort.
    """
    started = time.perf_counter()
    first_token, finish_reason, timings = None, "", {}
    with http_post_json(f"{url}/v1/chat/completions", body, api_key) as response:
        for raw in response:
            if cancel.is_set():
                return None
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[5:])
            timings = chunk.get("timings") or timings
            if on_progress and chunk.get("prompt_progress") and on_progress(chunk["prompt_progress"]):
                finish_reason = "prompt_time"
                break  # on_progress hat genug gesehen. Das Schließen der Verbindung beendet die Rechnung im Server.
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning_content")
                if piece:
                    first_token = first_token or time.perf_counter()
                    if on_token:
                        on_token(piece)
                finish_reason = choice.get("finish_reason") or finish_reason
            if time_limit and first_token and time.perf_counter() - first_token >= time_limit:
                finish_reason = "time"
                break  # Das Schließen der Verbindung beendet auch die Rechnung im Server.
    ended = time.perf_counter()
    return timings, (first_token or ended) - started, ended - started, finish_reason


def sweep_contexts(limit, trained):
    """Wählt die Messpunkte: ein Raster, dazu mehrere Punkte dort, wo der VRAM laut Schätzung voll wird."""
    top = min(trained or SWEEP_GRID[-1], max(SWEEP_GRID_DEFAULT_TOP, 3 * limit) if limit else SWEEP_GRID_DEFAULT_TOP)
    wanted = []
    if limit and limit >= SWEEP_GRID[0]:
        wanted += [limit, int(limit * 1.25) // 256 * 256]
    wanted.append(top)
    for value in SWEEP_GRID:  # Rasterpunkte zählen nur, wenn sie nicht direkt neben einem wichtigeren Punkt liegen.
        if value < top and all(abs(value - other) > other * 0.1 for other in wanted):
            wanted.append(value)
    return sorted(value for value in set(wanted) if value <= top)


def parse_contexts(text):
    """Liest eine Liste wie „16384, 32k 40000“. Wirft ValueError, wenn etwas nicht passt."""
    values = set()
    for item in re.split(r"[,;\s]+", text.strip()):
        match = re.fullmatch(r"(\d+)([kK]?)", item)
        if not match:
            raise ValueError(tr("„{item}“ ist keine Kontextgröße.", item=item))
        value = int(match.group(1)) * (1024 if match.group(2) else 1)
        if value < 1024:
            raise ValueError(tr("{value} ist zu klein, das Minimum ist 1024.", value=value))
        values.add(value)
    return sorted(values)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def vram_used_mib():
    """Belegter VRAM laut nvidia-smi, oder None ohne NVIDIA-Karte."""
    try:
        run = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        return int(run.stdout.splitlines()[0])
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None


def other_llama_servers(own_pid=None):
    """Zählt fremde llama-server. Sie belegen VRAM und würden die Messung verfälschen."""
    try:
        run = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, errors="replace", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    pids = re.findall(r'"llama-server\.exe","(\d+)"', run.stdout)
    return sum(1 for pid in pids if pid != str(own_pid))


def run_repeat_test(url, model_id, api_key, target, cancel, time_limit):
    """Lässt das Modell einen Text abschreiben, der schon im Prompt steht. Dort kann Spekulation am meisten bringen."""
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": f"Schreibe den folgenden Code unverändert ab. Gib nur den Code aus.\n\n{repeat_lines(target)}"}],
        "max_tokens": target,
        "temperature": 0,
        "seed": 42,
        "stream": True,
        "cache_prompt": False,
        "timings_per_token": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    reply = stream_chat(url, api_key, body, cancel, time_limit=time_limit)
    return speedtest_result(*reply) if reply else None


def run_speedtest(url, model_id, api_key, on_token, cancel, time_limit=None):
    """Lässt das Modell einen Text erzeugen, auf Wunsch nur time_limit Sekunden lang. Liefert die Messwerte, bei Abbruch None."""
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": SPEEDTEST_PROMPT}],
        "max_tokens": SPEEDTEST_MAX_TOKENS,
        "temperature": 0,
        "seed": 42,
        "stream": True,
        "cache_prompt": False,
        "timings_per_token": bool(time_limit),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    reply = stream_chat(url, api_key, body, cancel, on_token=on_token, time_limit=time_limit)
    return speedtest_result(*reply) if reply else None


def build_prompt_text(target, tokenize):
    """Erzeugt immer denselben Text mit ungefähr target Tokens. tokenize zählt die Tokens eines Texts."""
    rng = random.Random(42)
    text = " ".join(rng.choice(PROMPT_TEST_WORDS) for _ in range(target))
    count = tokenize(text)
    return text[:int(len(text) * target / count)] if count > target else text


def wait_until_idle(url, api_key, timeout=PROMPT_IDLE_TIMEOUT):
    """Wartet, bis kein Slot mehr rechnet. Sonst liefe die nächste Messung neben dem abgebrochenen Prompt her."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        status, body = http_get(f"{url}/slots", api_key, timeout=5)
        if status != 200:
            return status is not None  # Ohne Slots-Endpunkt lässt sich das nicht prüfen.
        try:
            if not any(slot.get("is_processing") for slot in json.loads(body)):
                return True
        except (ValueError, AttributeError):
            return True
        time.sleep(0.3)
    return False


def run_prompt_test(url, model_id, api_key, target, on_progress, cancel, time_limit=PROMPT_TEST_SECONDS):
    """Lässt das Modell einen langen Prompt einlesen. Liefert die Messwerte, bei Abbruch None.

    Nach time_limit Sekunden endet das Einlesen. Das Tempo stammt dann aus dem Fortschritt zwischen der ersten und der letzten Meldung,
    so zählt das Anlaufen der Anfrage nicht mit. Ist der Prompt vorher fertig, gelten die genauen Zeiten des Servers."""
    def tokenize(text):
        try:
            with http_post_json(f"{url}/tokenize", {"content": text}, api_key, timeout=60) as response:
                return len(json.load(response)["tokens"])
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            return len(text) // 4  # Grobe Schätzung, die genaue Länge meldet der Server am Ende.

    # Die Zeitmarke am Anfang verhindert, dass der Server Teile aus dem Cache nimmt.
    content = f"Lauf {time.time()}. {build_prompt_text(target, tokenize)}\nAntworte nur mit OK."
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": True,
        "cache_prompt": False,
        "return_progress": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    marks = []
    started = time.perf_counter()

    def progress(data):
        on_progress(data)
        done = data.get("processed", 0) - data.get("cache", 0)
        marks.append((time.perf_counter(), done))
        return bool(time_limit) and len(marks) >= 2 and marks[-1][1] > marks[0][1] and marks[-1][0] - started >= time_limit and done < data.get("total", 0)

    reply = stream_chat(url, api_key, body, cancel, on_progress=progress)
    if not reply:
        return None
    timings, _, total, finish = reply
    if finish == "prompt_time":
        (first_time, first_done), (last_time, last_done) = marks[0], marks[-1]
        wait_until_idle(url, api_key)
        return {"tokens": last_done, "prompt_tps": (last_done - first_done) / (last_time - first_time), "total": total, "partial": True}
    if not timings.get("prompt_n"):
        raise ValueError(tr("Der Server hat keine Messwerte geliefert."))
    return {"tokens": timings["prompt_n"], "prompt_tps": timings.get("prompt_per_second", 0), "total": total}


def read_vram_snapshot():
    """Liest die VRAM-Zähler von Windows und, falls vorhanden, die Gesamtwerte von nvidia-smi."""
    query = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", VRAM_QUERY], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    snapshot = json.loads(query.stdout)
    snapshot["nvidia"] = None
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.reserved,memory.free",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        snapshot["nvidia"] = [int(value) for value in smi.stdout.splitlines()[0].split(",")]
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        pass
    return snapshot


def summarize_vram(snapshot, own_pid=None):
    """Fasst die Zähler pro Programm zusammen und schätzt, was sich durch Schließen freigeben lässt."""
    adapters = snapshot.get("adapters") or []
    adapter = max(adapters, key=lambda a: a["dedicated"]) if adapters else None
    programs = {}
    for process in snapshot.get("processes") or []:
        if adapter and not process["key"].endswith(adapter["name"]):
            continue
        pid = process["key"].split("_")[1]
        name = process.get("name") or tr("Beendet (PID {pid})", pid=pid)
        lowered = name.lower()
        if own_pid is not None and pid == str(own_pid):
            kind = "own"
        elif lowered in VRAM_AI_PROGRAMS:
            kind = "ai"
        elif lowered in VRAM_SYSTEM_PROGRAMS:
            kind = "system"
        else:
            kind = "app"
        entry = programs.setdefault((name, kind), 0)
        programs[(name, kind)] = entry + process["dedicated"] / 1024 ** 2
    rows = [{"name": name, "kind": kind, "mib": mib} for (name, kind), mib in programs.items() if mib >= 1]
    rows.sort(key=lambda row: (VRAM_KIND_ORDER[row["kind"]], -row["mib"]))
    nvidia = snapshot.get("nvidia")
    used = nvidia[1] if nvidia else (adapter["dedicated"] / 1024 ** 2 if adapter else 0)
    closable = sum(row["mib"] for row in rows if row["kind"] in ("app", "ai"))
    return {
        "total": nvidia[0] if nvidia else None,
        "used": used,
        "reserved": nvidia[2] if nvidia else None,
        "free": nvidia[3] if nvidia else None,
        "rows": rows,
        "freeable": min(closable, used),
        "gpus": snapshot.get("gpus") or [],
    }


def http_get(url, api_key="", timeout=2):
    """Lokale Anfrage ohne System-Proxy. Liefert (Status, Antworttext)."""
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, ""
    except OSError:
        return None, ""


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.body = ttk.Frame(self.canvas, padding=8)
        window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))

    def wheel(self, delta):
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-delta // 120, "units")


class ColorButton(tk.Button):
    """Großer, kräftig gefärbter Knopf. ttk-Knöpfe lassen sich unter Windows nicht einfärben."""

    def __init__(self, parent, color, pressed, state="normal", **options):
        self.color = color
        super().__init__(parent, foreground="white", activeforeground="white", activebackground=pressed, disabledforeground="#8a8a88",
                         relief="flat", borderwidth=0, font=("Segoe UI", 13, "bold"), padx=26, pady=8, **options)
        self.configure(state=state)

    def configure(self, cnf=None, **options):
        if "state" in options:
            disabled = options["state"] == "disabled"
            options.update(background="#d8d8d6" if disabled else self.color, cursor="arrow" if disabled else "hand2")
        return super().configure(cnf, **options)


class App(tk.Tk):
    def __init__(self, config_path=CONFIG_PATH):
        super().__init__()
        self.title("NoProbLlama")
        try:
            self.iconbitmap(default=str(ICON_PATH))  # default gilt auch für Chat und alle anderen Fenster.
        except tk.TclError:
            pass
        self.geometry("1180x940")
        self.minsize(1060, 720)
        self.config_path = config_path
        self.closed = False
        self.config_data = self._load_config()
        self.events = queue.Queue()
        self.proc = None
        self.server_ready = False
        self.server_ctx = None
        self.server_url = ""
        self.server_settings = {}
        self.chat_window = None
        self.stop_requested = False
        self.speed_cancel = None
        self.sweep_cancel = None
        self.sweep_proc = None
        self.sweep_current = None
        self.sweep_pending = False
        self.sweep_restart = False
        self.optimize_window = None
        self.optimize_pending = False
        self.optimize_restart = False
        self.sweep_hover = None
        self.speed_buttons = []
        self.model_info = {}
        self.model_paths = {}
        self.build_number = ""
        self.vars = {}
        self.scroll_frames = []
        self.help_window = None
        self.vram_window = None
        self.vram_first_free = None
        self.vram_free = None
        self._preview_pending = False

        # Comboboxen sollen beim Scrollen der Liste ihren Wert behalten.
        self.unbind_class("TCombobox", "<MouseWheel>")
        self.bind_all("<MouseWheel>", self._on_wheel)

        settings = self.config_data.get("paths", {})
        self.llama_dir = tk.StringVar(value=settings.get("llama_dir", DEFAULT_LLAMA_DIR))
        self.models_dir = tk.StringVar(value=settings.get("models_dir", DEFAULT_MODELS_DIR))
        self.model_var = tk.StringVar()
        self.profile_var = tk.StringVar()
        self.status_var = tk.StringVar(value=tr("Gestoppt"))
        self.info_var = tk.StringVar()
        self.warn_var = tk.StringVar()
        self.vram_estimate_var = tk.StringVar()
        self.gpu_var =tk.StringVar(value=tr("Geräte werden geprüft…"))

        self._build_ui()
        current = self.config_data.get("current", DEFAULTS)
        # Einmalige Umstellung bestehender Einstellungen auf die neue Automatik; Profile bleiben unverändert.
        if self.config_data.get("version", 1) < 2:
            current = {**current, "spec_type": "auto"}
        # Ebenso einmalig die kleinere Fit-Reserve. Ein eigener anderer Wert bleibt erhalten.
        if self.config_data.get("version", 1) < 3 and current.get("fit_target", "").strip() in ("", "512", "1024"):
            current = {**current, "fit_target": "256"}
        self._load_values(current)
        self.refresh_models(self.config_data.get("current", {}).get("_model", ""))
        self._refresh_profiles()
        self._detect_devices()
        self._schedule_preview()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._pump_id = self.after(100, self._pump)

    # ---------- Oberfläche ----------

    def _build_ui(self):
        style = ttk.Style(self)
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Flag.TLabel", foreground="#1a5fb4", font=("Consolas", 9))
        style.configure("Warn.TLabel", foreground="#b35900")
        style.configure("Big.TButton", font=("Segoe UI", 11), padding=(14, 9))
        style.configure("TNotebook.Tab", font=("Segoe UI", 11), padding=(16, 8))
        style.configure("Head.TLabel", font=("Segoe UI", 9, "bold"))
        style.configure("Help.TButton", padding=0)
        for level, color in enumerate(METER_COLORS):
            style.configure(f"Meter{level}.TLabel", foreground=color, font=("Segoe UI Symbol", 9))

        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text=tr("llama.cpp-Ordner")).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.llama_dir).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(top, text=tr("Ordner…"), command=lambda: self._pick_dir(self.llama_dir, self._detect_devices)).grid(
            row=0, column=2, padx=(6, 0))

        ttk.Label(top, text=tr("Modell-Ordner")).grid(row=1, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.models_dir).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(top, text=tr("Ordner…"), command=lambda: self._pick_dir(self.models_dir, self.refresh_models)).grid(
            row=1, column=2, padx=(6, 0))

        ttk.Label(top, text=tr("Modell")).grid(row=2, column=0, sticky="w", padx=(0, 6))
        self.model_box = ttk.Combobox(top, textvariable=self.model_var, state="readonly")
        self.model_box.grid(row=2, column=1, sticky="ew", pady=2)
        self.model_box.bind("<<ComboboxSelected>>", lambda _: self._on_model_change())
        ttk.Button(top, text=tr("Neu einlesen"), command=self.refresh_models).grid(row=2, column=2, padx=(6, 0))

        ttk.Label(top, textvariable=self.info_var, style="Hint.TLabel").grid(
            row=3, column=1, columnspan=2, sticky="w")
        self.vram_estimate_label = ttk.Label(top, textvariable=self.vram_estimate_var, justify="left")
        self.vram_estimate_label.grid(row=4, column=1, columnspan=2, sticky="w")

        ttk.Label(top, text=tr("Profil")).grid(row=5, column=0, sticky="w", padx=(0, 6))
        row = ttk.Frame(top)
        row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=2)
        self.profile_box = ttk.Combobox(row, textvariable=self.profile_var, width=32)
        self.profile_box.pack(side="left")
        self.profile_box.bind("<<ComboboxSelected>>", lambda _: self.load_profile())
        ttk.Button(row, text=tr("Speichern"), command=self.save_profile).pack(side="left", padx=(6, 0))
        ttk.Button(row, text=tr("Löschen"), command=self.delete_profile).pack(side="left", padx=(6, 0))
        ttk.Button(row, text=tr("Alles zurücksetzen"), command=self.reset_values).pack(side="left", padx=(6, 0))
        self.language_names = llama_lang.available()
        self.language_var = tk.StringVar(value=self.language_names[llama_lang.current])
        language_box = ttk.Combobox(row, textvariable=self.language_var, values=list(self.language_names.values()), state="readonly", width=10)
        language_box.pack(side="right")
        language_box.bind("<<ComboboxSelected>>", lambda _: self._change_language())
        ttk.Label(row, text="Sprache / Language").pack(side="right", padx=(0, 6))  # Absichtlich zweisprachig, damit man sie in jeder Sprache findet.

        self.gpu_frame = tk.Frame(self, background="#eef3f8")
        self.gpu_frame.pack(fill="x", padx=10, pady=(2, 4))
        self.gpu_label = tk.Label(self.gpu_frame, textvariable=self.gpu_var, background="#eef3f8",
                                  anchor="w", justify="left", padx=8, pady=4)
        self.gpu_label.pack(side="left", fill="x", expand=True)
        self.gpu_button = ttk.Button(self.gpu_frame, text=tr("CUDA-Paket im Browser öffnen"),
                                     command=self._open_release_page)

        controls = ttk.Frame(self, padding=(10, 6))
        controls.pack(side="top", fill="x")
        self.start_button = ColorButton(controls, "#1f8a3d", "#17702f", text=tr("▶  Start"), command=self.start_server)
        self.start_button.pack(side="left", fill="y")
        self.stop_button = ColorButton(controls, "#c0392b", "#a32d21", text=tr("■  Stop"), command=self.stop_server, state="disabled")
        self.stop_button.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(controls, text=tr("Chat"), style="Big.TButton", command=self.open_chat).pack(side="left", padx=(16, 0))
        self.web_button = ttk.Button(controls, text=tr("Web-UI öffnen"), style="Big.TButton", command=self.open_webui, state="disabled")
        self.web_button.pack(side="left", padx=(6, 0))
        self.optimize_button = ttk.Button(controls, text=tr("Modell optimieren"), style="Big.TButton", command=self.open_optimizer)
        self.optimize_button.pack(side="left", padx=(6, 0))
        ttk.Button(controls, text=tr("VRAM prüfen"), style="Big.TButton", command=self.show_vram_check).pack(side="left", padx=(6, 0))
        self.status_dot = tk.Label(controls, text="●", fg="#888888", font=("Segoe UI", 13))
        self.status_dot.pack(side="left", padx=(18, 4))
        ttk.Label(controls, textvariable=self.status_var).pack(side="left")

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        upper = ttk.Frame(panes)
        panes.add(upper, weight=4)
        ttk.Label(upper, style="Hint.TLabel", text=(
            tr("Wirkung:  ●●● groß   ●●○ mittel   ●○○ gering   ○○○ keine   ·   Die wirkungsvollsten Einstellungen stehen oben   ·   "
            "Richtwerte, die Wirkung hängt von Modell und Hardware ab"))).pack(anchor="w", pady=(0, 3))
        self.notebook = notebook = ttk.Notebook(upper)
        notebook.pack(fill="both", expand=True)
        for title, opts in TABS.items():
            frame = ScrollFrame(notebook)
            notebook.add(frame, text=title)
            self.scroll_frames.append(frame)
            self._build_tab(frame.body, opts)
        self._build_speed_tab(notebook)
        self._build_sweep_tab(notebook)
        notebook.bind("<<NotebookTabChanged>>", lambda _: self._on_tab_changed())

        preview = ttk.Frame(upper)
        # Vor dem Notebook eingereiht, damit bei wenig Platz die Tabs schrumpfen und nicht die Vorschau.
        preview.pack(side="bottom", fill="x", pady=(6, 0), before=notebook)
        head = ttk.Frame(preview)
        head.pack(fill="x")
        ttk.Label(head, text=tr("Befehl (leere Felder nutzen den llama.cpp-Standard)")).pack(side="left")
        ttk.Button(head, text=tr("Kopieren"), command=self.copy_command).pack(side="right")
        self.preview = tk.Text(preview, height=3, wrap="word", font=("Consolas", 9), state="disabled",
                               background="#f4f4f4", relief="flat", padx=6, pady=4)
        self.preview.pack(fill="x", pady=(3, 0))
        self.warn_label = ttk.Label(preview, textvariable=self.warn_var, style="Warn.TLabel", wraplength=1050)
        self.warn_label.pack(fill="x")

        lower = ttk.Frame(panes)
        panes.add(lower, weight=1)
        head = ttk.Frame(lower)
        head.pack(fill="x")
        ttk.Label(head, text=tr("Server-Log")).pack(side="left")
        ttk.Button(head, text=tr("Leeren"), command=self.clear_log).pack(side="right")
        body = ttk.Frame(lower)
        body.pack(fill="both", expand=True, pady=(3, 0))
        self.log = tk.Text(body, wrap="none", font=("Consolas", 9), state="disabled", height=6,
                           background="#1e1e1e", foreground="#d4d4d4", relief="flat", padx=6, pady=4)
        bar = ttk.Scrollbar(body, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        self.log.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.log.tag_configure("warn", foreground="#e5c07b")
        self.log.tag_configure("error", foreground="#f28b82")
        self.log.tag_configure("app", foreground="#7fb4ff")

    def _build_speed_tab(self, notebook):
        self.speed_tab = tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text=tr("Speedtest"))

        head = ttk.Frame(tab)
        head.pack(fill="x")
        button = ttk.Button(head, text=tr("Speedtest starten"), style="Big.TButton", command=self.toggle_speedtest, state="disabled")
        button.pack(side="left")
        self.speed_buttons.append(button)
        self.speed_bar =ttk.Progressbar(head, maximum=SWEEP_ANSWER_SECONDS, length=150)
        self.speed_bar.pack(side="left", padx=(10, 0))
        self.speed_status = tk.StringVar(value=tr("Liest bis zu {prompt_test_tokens} Tokens ein, höchstens {prompt_test_seconds} Sekunden lang, und misst dann {sweep_answer_seconds} Sekunden lang die Antwort.", prompt_test_tokens=PROMPT_TEST_TOKENS, prompt_test_seconds=PROMPT_TEST_SECONDS, sweep_answer_seconds=SWEEP_ANSWER_SECONDS))
        ttk.Label(head, textvariable=self.speed_status).pack(side="left", padx=(10, 0))
        ttk.Button(head, text=tr("Verlauf leeren"), command=self.clear_speedtests).pack(side="right")

        columns = {
            "time": (tr("Zeit"), 85), "model": (tr("Modell"), 170), "ctx": (tr("Kontext"), 60), "ngl": (tr("GPU-Layer"), 65),
            "spec": (tr("Speculative"), 125), "ubatch": (tr("Micro-Batch"), 75), "prompt_tokens": (tr("Prompt-Tokens"), 95),
            "prompt_tps": (tr("Prompt Tok/s"), 80), "tokens": (tr("Tokens"), 50), "tps": ("Tok/s", 50), "first": (tr("1. Token (s)"), 70),
            "total": (tr("Dauer (s)"), 60), "draft": (tr("Draft-Quote"), 70),
        }
        table = ttk.Frame(tab)
        table.pack(fill="both", expand=True, pady=(6, 0))
        self.speed_table = ttk.Treeview(table, columns=list(columns), show="headings", height=5)
        for key, (title, width) in columns.items():
            self.speed_table.heading(key, text=title)
            self.speed_table.column(key, width=width, anchor="w" if key == "model" else "center",
                                    stretch=key == "model")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.speed_table.yview)
        self.speed_table.configure(yscrollcommand=bar.set)
        self.speed_table.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        self.speed_text = tk.Text(tab, height=4, wrap="word", state="disabled", font=("Segoe UI", 9),
                                  background="#f4f4f4", relief="flat", padx=6, pady=4)
        self.speed_text.pack(fill="x", pady=(6, 0))
        for row in self.config_data.get("speedtests", []):
            self._add_speed_row(row)

    def _build_sweep_tab(self, notebook):
        self.sweep_tab = tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text=tr("Kontext-Test"))

        head = ttk.Frame(tab)
        head.pack(fill="x")
        self.sweep_button = ttk.Button(head, text=tr("Kontext-Test starten"), command=self.toggle_sweep)
        self.sweep_button.pack(side="left")
        ttk.Label(head, text=tr("Kontexte")).pack(side="left", padx=(12, 4))
        self.sweep_contexts_var = tk.StringVar()
        self.sweep_contexts_var.trace_add("write", lambda *_: self._schedule_preview())
        ttk.Entry(head, textvariable=self.sweep_contexts_var, width=30).pack(side="left")
        self.sweep_hint = tk.StringVar()
        ttk.Label(head, textvariable=self.sweep_hint, style="Hint.TLabel").pack(side="left", padx=(8, 0))

        status = ttk.Frame(tab)
        status.pack(fill="x", pady=(6, 0))
        self.sweep_bar = ttk.Progressbar(status, length=150)
        self.sweep_bar.pack(side="left")
        self.sweep_status = tk.StringVar(value=tr("Startet das Modell pro Messpunkt neu. Das dauert, die Ergebnisse bleiben aber gespeichert."))
        ttk.Label(status, textvariable=self.sweep_status).pack(side="left", padx=(10, 0))

        saved = ttk.Frame(tab)
        saved.pack(fill="x", pady=(6, 0))
        ttk.Label(saved, text=tr("Gespeichert")).pack(side="left", padx=(0, 4))
        self.sweep_choice = tk.StringVar()
        self.sweep_box = ttk.Combobox(saved, textvariable=self.sweep_choice, state="readonly", width=70)
        self.sweep_box.pack(side="left")
        self.sweep_box.bind("<<ComboboxSelected>>", lambda _: self._show_saved_sweep())
        ttk.Button(saved, text=tr("Bild öffnen"), command=self.open_sweep_image).pack(side="left", padx=(6, 0))
        ttk.Button(saved, text=tr("Löschen"), command=self.delete_sweep).pack(side="left", padx=(6, 0))

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True, pady=(6, 0))
        columns = {"ctx": (tr("Kontext"), 62), "prompt_tps": (tr("Prompt Tok/s"), 90), "tps": ("Tok/s", 48), "vram": (tr("VRAM MiB"), 72),
                   "state": (tr("Status"), 54)}
        table = ttk.Frame(body)
        table.pack(side="right", fill="y", padx=(8, 0))
        self.sweep_table = ttk.Treeview(table, columns=list(columns), show="headings", height=6)
        for key, (title, width) in columns.items():
            self.sweep_table.heading(key, text=title)
            self.sweep_table.column(key, width=width, anchor="center", stretch=False)
        self.sweep_table.pack(fill="y", expand=True)
        self.sweep_plot = ttk.Frame(body)
        self.sweep_plot.pack(side="left", fill="both", expand=True)
        self.sweep_figure = None
        self.sweep_canvas = None
        self.sweep_axes = None
        self.sweep_shown = None
        self._refresh_saved_sweeps()

    def _on_tab_changed(self):
        self.notebook.focus_set()  # Sonst liegt der Fokus auf dem ersten Knopf im Reiter, und schon die Leertaste löst ihn aus.
        # matplotlib lädt erst hier, damit das Programm weiterhin schnell startet.
        if self.notebook.select() == str(self.sweep_tab):
            self._ensure_sweep_canvas()
            self._draw_sweep()

    def _build_tab(self, parent, opts):
        parent.columnconfigure(6, weight=1)
        for column, title in enumerate((tr("Einstellung"), tr("Wert"), tr("Parameter"), tr("Tempo"), tr("Qualität"), tr("Speicher"), tr("Was es bringt"))):
            ttk.Label(parent, text=title, style="Head.TLabel").grid(
                row=0, column=column, sticky="w", padx=METER_PADDING if 3 <= column <= 5 else (0, 10), pady=(0, 4))
        hints = []
        for row, opt in enumerate(opts, start=1):
            var = self.vars[opt.key] = tk.StringVar()
            var.trace_add("write", lambda *_: self._schedule_preview())
            ttk.Label(parent, text=opt.label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
            self._build_field(parent, opt, var).grid(row=row, column=1, sticky="ew", pady=2, padx=(0, 10))
            ttk.Label(parent, text=opt.flag, style="Flag.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 10))
            for column, level in zip((3, 4, 5), (opt.speed, opt.quality, opt.memory)):
                ttk.Label(parent, text=meter_text(level), style=f"Meter{level}.TLabel").grid(
                    row=row, column=column, sticky="w", padx=METER_PADDING)
            hint = opt.hint + (tr(" Standard: {default}.", default=opt.default) if opt.default else "")
            label = ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=340)
            label.grid(row=row, column=6, sticky="w")
            hints.append(label)
            ttk.Button(parent, text="?", width=2, style="Help.TButton",
                       command=lambda o=opt: self.show_help(o)).grid(row=row, column=7, padx=(8, 2))
        parent.bind("<Configure>", lambda event: self._fit_hints(parent, hints, event.width), add="+")

    @staticmethod
    def _fit_hints(parent, hints, width):
        """Die Erklärungen nutzen die Breite, die neben den festen Spalten übrig bleibt."""
        left = parent.grid_bbox(6, 0)[0]
        wrap = max(200, width - left - 48)
        if hints and hints[0].cget("wraplength") != wrap:
            for label in hints:
                label.configure(wraplength=wrap)

    def show_help(self, opt):
        """Zeigt die ausführliche Erklärung. Das Fenster bleibt offen und wechselt nur den Inhalt."""
        if self.help_window is None or not self.help_window.winfo_exists():
            self.help_window = tk.Toplevel(self)
            self.help_window.title(tr("Parameter-Hilfe"))
            self.help_window.geometry(f"560x520+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 80}")
            self.help_window.transient(self)
            self.help_window.bind("<Escape>", lambda _: self.help_window.destroy())
            self.help_text = tk.Text(self.help_window, wrap="word", state="disabled", relief="flat", padx=14, pady=12,
                                     font=("Segoe UI", 10), spacing2=2)
            bar = ttk.Scrollbar(self.help_window, orient="vertical", command=self.help_text.yview)
            self.help_text.configure(yscrollcommand=bar.set)
            self.help_apply = ttk.Button(self.help_window, text=tr("Vorschlag übernehmen"), command=self.apply_moe_suggestion)
            bar.pack(side="right", fill="y")
            self.help_text.pack(fill="both", expand=True)
            self.help_text.tag_configure("title", font=("Segoe UI", 14, "bold"))
            self.help_text.tag_configure("flag", font=("Consolas", 10), foreground="#1a5fb4")
            self.help_text.tag_configure("head", font=("Segoe UI", 10, "bold"), spacing1=10)
            for level, color in enumerate(METER_COLORS):
                self.help_text.tag_configure(f"meter{level}", foreground=color, font=("Segoe UI Symbol", 10))
        text = self.help_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", opt.label + "\n", "title")
        if opt.flag:
            flags = f"{opt.flag}   /   {opt.off_flag}" if opt.off_flag else opt.flag
            text.insert("end", flags + "\n", "flag")
        text.insert("end", tr("\nWas es tut\n"), "head")
        text.insert("end", opt.what + "\n")
        if opt.key == "ctx_size":
            text.insert("end", tr("\nVRAM-Bedarf für dieses Modell\n"), "head")
            text.insert("end", self._context_table() + "\n")
        if opt.key in MOE_KEYS:
            text.insert("end", tr("\nWas du für dieses Modell eintragen solltest\n"), "head")
            text.insert("end", self._moe_advice() + "\n")
        sections = ((tr("Tempo"), opt.speed, opt.speed_note), (tr("Qualität"), opt.quality, opt.quality_note),
                    (tr("Speicher"), opt.memory, opt.memory_note))
        for title, level, note in sections:
            text.insert("end", f"\n{title}   ", "head")
            text.insert("end", f"{meter_text(level)}  {METER_NAMES[level]}\n", f"meter{level}")
            text.insert("end", (note or tr("Kein spürbarer Einfluss.")) + "\n")
        if opt.tip:
            text.insert("end", tr("\nEmpfehlung\n"), "head")
            text.insert("end", opt.tip + "\n")
        if opt.default:
            text.insert("end", tr("\nStandard\n"), "head")
            text.insert("end", opt.default + "\n")
        text.configure(state="disabled")
        text.yview_moveto(0)
        suggestion = self._moe_suggestion()
        if opt.key in MOE_KEYS and suggestion and suggestion["on_gpu"]:
            label = tr("Vorschlag übernehmen: -ngl all und -ncmoe {n_cpu_moe}", n_cpu_moe=suggestion['n_cpu_moe']) if suggestion["n_cpu_moe"] \
                else tr("Vorschlag übernehmen: -ngl all")
            self.help_apply.configure(text=label)
            self.help_apply.pack(side="bottom", pady=8, before=text)
        else:
            self.help_apply.pack_forget()
        self.help_window.deiconify()
        self.help_window.lift()
        self.help_window.focus_set()

    def _build_field(self, parent, opt, var):
        if opt.kind == "flag":
            return ttk.Checkbutton(parent, variable=var, onvalue="1", offvalue="")
        if opt.kind == "toggle":
            return ttk.Combobox(parent, textvariable=var, values=("", "an", "aus"), state="readonly", width=18)
        if opt.kind == "choice":
            return ttk.Combobox(parent, textvariable=var, values=opt.values, state="readonly", width=18)
        if opt.kind == "combo":
            return ttk.Combobox(parent, textvariable=var, values=opt.values, width=18)
        if opt.kind == "file":
            frame = ttk.Frame(parent)
            ttk.Entry(frame, textvariable=var, width=16).pack(side="left", fill="x", expand=True)
            ttk.Button(frame, text="…", width=3, command=lambda: self._pick_file(var)).pack(side="left", padx=(2, 0))
            return frame
        return ttk.Entry(parent, textvariable=var, width=20)

    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        for frame in self.scroll_frames:
            if str(widget).startswith(str(frame)):
                frame.wheel(event.delta)
                return

    def _pick_dir(self, var, callback):
        path = filedialog.askdirectory(initialdir=var.get() or None)
        if path:
            var.set(str(Path(path)))
            callback()

    def _pick_file(self, var):
        path = filedialog.askopenfilename(initialdir=self.models_dir.get() or None)
        if path:
            var.set(str(Path(path)))

    # ---------- Werte, Modelle, Profile ----------

    def values(self):
        return {key: var.get() for key, var in self.vars.items()}

    def selected_model(self):
        return self.model_paths.get(self.model_var.get())

    def _load_values(self, values):
        for key, var in self.vars.items():
            var.set(values.get(key, ""))

    def refresh_models(self, select=""):
        previous = select or (str(self.selected_model()) if self.selected_model() else "")
        models, mmprojs = scan_models(self.models_dir.get())
        self.model_paths = {f"{p.name}   ({model_size_gib(p):.2f} GiB)": p for p in models}
        labels = list(self.model_paths)
        self.model_box.configure(values=labels)
        match = next((label for label, p in self.model_paths.items() if str(p) == previous), None)
        self.model_var.set(match or (labels[0] if labels else ""))
        if mmprojs and not self.vars["mmproj"].get():
            self._log_app(tr("Multimodal-Projektor gefunden, auswählbar unter „Chat & Server“: {mmprojs}", mmprojs=mmprojs[0]))
        self._on_model_change()

    def _on_model_change(self):
        path = self.selected_model()
        self.model_info = {}
        self.info_var.set(tr("Metadaten werden gelesen…") if path else tr("Keine GGUF-Datei im Modell-Ordner gefunden."))
        self._schedule_preview()
        if not path:
            return

        def work():
            try:
                info = read_gguf_info(path)
            except (OSError, ValueError, struct.error, OverflowError, MemoryError) as err:
                self.events.put(lambda message=tr("Metadaten nicht lesbar: {err}", err=err): self.info_var.set(message))
                return
            self.events.put(lambda: self._set_model_info(path, info))

        threading.Thread(target=work, daemon=True).start()

    def _set_model_info(self, path, info):
        if path != self.selected_model():
            return
        self.model_info = info
        self._apply_model_rules(info)
        parts = [info["name"], info["arch"], info["size_label"]]
        if info["context"]:
            parts.append(tr("max. Kontext {context}", context=format_mib(info['context'])))
        parts.append(tr("MTP: ja") if info["mtp_layers"] else tr("MTP: nein"))
        if info["experts_used"]:
            parts.append(tr("MoE: {experts_used} von {experts} Experten aktiv", experts_used=info['experts_used'], experts=info['experts']))
        else:
            parts.append(tr("MoE: ja") if info["experts"] else tr("MoE: nein"))
        parts.append(tr("Tool-Aufrufe: ja") if info["tools"] else tr("Tool-Aufrufe: nein"))
        self.info_var.set("  ·  ".join(p for p in parts if p))
        self._schedule_preview()

    def _apply_model_rules(self, info):
        """Entfernt Einstellungen, die zum gewählten Modell nicht passen und --fit sonst blockieren würden."""
        moe_set = any(self.vars[key].get().strip() for key in MOE_KEYS)
        if moe_set and not info["experts"]:
            for key in MOE_KEYS:
                self.vars[key].set("")
            removed = tr("MoE-Felder")
            if self.vars["ngl"].get().strip() == "all":  # Stammt aus dem MoE-Vorschlag und würde --fit abschalten.
                self.vars["ngl"].set("")
                removed += tr(" und -ngl all")
            self._log_app(tr("{name} hat keine Experten: {removed} zurückgesetzt.", name=info['name'], removed=removed))
            return
        n_cpu_moe = self.vars["n_cpu_moe"].get().strip()
        if info["expert_layers"] and n_cpu_moe.isdigit() and int(n_cpu_moe) > info["expert_layers"]:
            self.vars["n_cpu_moe"].set("")
            self._log_app(tr("-ncmoe {n_cpu_moe} zurückgesetzt, dieses Modell hat nur {expert_layers} Expertenschichten.", n_cpu_moe=n_cpu_moe, expert_layers=info['expert_layers']))

    def _refresh_profiles(self):
        self.profile_box.configure(values=sorted(self.config_data.get("profiles", {})))

    def save_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showinfo(tr("Profil"), tr("Bitte zuerst einen Profilnamen ins Feld eintragen."))
            return
        data = self.values()
        data["_model"] = str(self.selected_model() or "")
        self.config_data.setdefault("profiles", {})[name] = data
        self._save_config()
        self._refresh_profiles()

    def load_profile(self):
        data = self.config_data.get("profiles", {}).get(self.profile_var.get())
        if data:
            self._load_values(data)
            self.refresh_models(data.get("_model", ""))

    def delete_profile(self):
        if self.config_data.get("profiles", {}).pop(self.profile_var.get(), None) is not None:
            self.profile_var.set("")
            self._save_config()
            self._refresh_profiles()

    def reset_values(self):
        self._load_values(DEFAULTS)

    def _load_config(self):
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        self.config_data["version"] = 3
        self.config_data["paths"] = {"llama_dir": self.llama_dir.get(), "models_dir": self.models_dir.get()}
        current = self.values()
        current["_model"] = str(self.selected_model() or "")
        self.config_data["current"] = current
        self.config_path.write_text(json.dumps(self.config_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---------- Vorschau ----------

    def exe_path(self):
        return Path(self.llama_dir.get()) / "llama-server.exe"

    def current_args(self):
        values = self.values()
        values["spec_type"] = resolve_spec_type(values["spec_type"], self.model_info, self.vram_free,
                                                fit_reserve_mib(values["fit_target"]))
        return build_args(self.exe_path(), self.selected_model(), values)

    def _schedule_preview(self):
        if not self._preview_pending and hasattr(self, "preview"):
            self._preview_pending = True
            self.after_idle(self._update_preview)

    def _update_preview(self):
        self._preview_pending = False
        try:
            command = subprocess.list2cmdline(self.current_args())
        except ValueError:
            command = tr("Weitere Argumente: Anführungszeichen nicht geschlossen")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", command)
        self.preview.configure(state="disabled")
        self.warn_var.set("\n".join(self._warnings()))
        self._update_sweep_hint()
        estimate = self._vram_estimate()
        if estimate:
            text, level = vram_estimate_text(estimate)
            self.vram_estimate_var.set(text)
            self.vram_estimate_label.configure(foreground=VRAM_LEVEL_COLORS[level])
        else:
            self.vram_estimate_var.set("")

    def _vram_estimate(self, values=None):
        if not self.model_info:
            return None
        values = values or self.values()
        spec = resolve_spec_type(values["spec_type"], self.model_info, self.vram_free, fit_reserve_mib(values["fit_target"]))
        return estimate_vram(self.model_info, values, self.vram_free, spec)

    def _context_table(self):
        """Zeigt für das gewählte Modell, was verschiedene Kontextgrößen im VRAM belegen."""
        if not self.model_info:
            return tr("Die Modelldaten werden noch gelesen.")
        lines = []
        for context in CONTEXT_TABLE:
            if self.model_info["context"] and context > self.model_info["context"]:
                continue
            est = self._vram_estimate({**self.values(), "ctx_size": str(context)})
            if est["usable"] is None:
                result = ""
            elif est["over"]:
                result = tr("   {over} MiB zu viel", over=format_mib(est['over']))
            else:
                result = tr("   passt")
            lines.append(tr("{context:>8} Tokens:   Kontext {kv} MiB,   gesamt {needed} MiB{result}", context=format_mib(context), kv=format_mib(est['kv']), needed=format_mib(est['needed']), result=result))
        est = self._vram_estimate()
        note = tr("\nNutzbar sind {usable} MiB.", usable=format_mib(est['usable'])) if est["usable"] is not None else ""
        return "\n".join(lines) + note + tr(" Die Rechnung berücksichtigt das Modell, die KV-Typen und die MoE-Felder.")

    def _warnings(self):
        values, info, out = self.values(), self.model_info, []
        if info.get("missing_parts"):
            out.append(tr("Von diesem Modell fehlen {missing_parts} Teildateien, der Start wird scheitern.", missing_parts=info['missing_parts']))
        if "draft-mtp" in values["spec_type"] and info and not info["mtp_layers"]:
            out.append(tr("draft-mtp: Dieses Modell hat keinen MTP-Layer."))
        moe_set = values["cpu_moe"] or values["n_cpu_moe"].strip() or values["moe_cache"].strip()
        if moe_set and info and not info["experts"]:
            out.append(tr("MoE-Optionen: Dieses Modell hat keine Experten, sie bleiben wirkungslos."))
        if values["cpu_moe"] and values["n_cpu_moe"].strip():
            out.append(tr("-cmoe legt schon alle Experten in den RAM, -ncmoe bringt daneben nichts. Bitte nur eins davon nutzen."))
        suggestion = self._moe_suggestion()
        if suggestion and not moe_set and suggestion["n_cpu_moe"]:
            out.append(tr("Tipp für dieses MoE-Modell: -ngl all und -ncmoe {n_cpu_moe}. Übernehmen geht im ?-Fenster bei "
                "„MoE: Layer mit Experten im RAM“.", n_cpu_moe=suggestion['n_cpu_moe']))
        ctx = values["ctx_size"].strip()
        if ctx.isdigit() and info.get("context") and int(ctx) > info["context"]:
            out.append(tr("Kontextgröße liegt über dem Trainingskontext des Modells ({context}).", context=info['context']))
        host = values["host"].strip()
        if host and client_host(host) != host.split(",")[0].strip() and not values["api_key"].strip():
            out.append(tr("Der Server ist im Netzwerk erreichbar und hat keinen API-Key."))
        return out

    def _moe_suggestion(self):
        if not self.model_info or self.vram_free is None:
            return None
        ctx = self.vars["ctx_size"].get().strip()
        context = int(ctx) if ctx.isdigit() and int(ctx) > 0 else 32768
        return moe_suggestion(self.model_info, self.vram_free, context, fit_reserve_mib(self.vars["fit_target"].get()),
                              self.vars["cache_k"].get().strip(), self.vars["cache_v"].get().strip())

    def _moe_advice(self):
        """Erklärt, was für das gewählte Modell einzutragen ist."""
        if not self.model_info:
            return tr("Die Modelldaten werden noch gelesen.")
        if not self.model_info["experts"]:
            return tr("Das ausgewählte Modell hat keine Experten. Lass das Feld leer.")
        if self.vram_free is None:
            return tr("Der freie VRAM ist nicht bekannt, deshalb gibt es hier keinen Vorschlag.")
        s = self._moe_suggestion()
        facts = (tr("Experten: {expert_mib:.0f} MiB in {layers} Layern, also {layer_mib:.0f} MiB pro Layer. Frei sind "
            "{free_mib} MiB VRAM, die immer aktiven Teile und der Kontext brauchen etwa {needed_mib:.0f} MiB, und "
            "--fit hält {reserve_mib} MiB Reserve.", expert_mib=s['expert_mib'], layers=s['layers'], layer_mib=s['layer_mib'], free_mib=s['free_mib'], needed_mib=s['needed_mib'], reserve_mib=s['reserve_mib']))
        if not s["n_cpu_moe"]:
            return facts + tr("\nDas Modell passt komplett in den VRAM, die MoE-Felder können leer bleiben.")
        if not s["on_gpu"]:
            return facts + tr("\nEs passt keine ganze Expertenschicht in den VRAM. Nutze -cmoe, und verkleinere bei Bedarf den Kontext.")
        running = tr("\nLäuft gerade ein Server, ist der freie VRAM zu niedrig angesetzt.") if self.proc else ""
        return (facts + tr("\nDamit passen etwa {on_gpu} Expertenschichten auf die GPU. Vorschlag: -ngl all und -ncmoe "
            "{n_cpu_moe}. Das ist eine Schätzung, prüfe sie im Speedtest.", on_gpu=s['on_gpu'], n_cpu_moe=s['n_cpu_moe']) + running)

    def apply_moe_suggestion(self):
        suggestion = self._moe_suggestion()
        if not suggestion:
            return
        self.vars["ngl"].set("all")
        self.vars["cpu_moe"].set("")
        self.vars["moe_cache"].set("")
        self.vars["n_cpu_moe"].set(str(suggestion["n_cpu_moe"]) if suggestion["n_cpu_moe"] else "")

    def copy_command(self):
        self.clipboard_clear()
        self.clipboard_append(self.preview.get("1.0", "end").strip())

    # ---------- Geräteerkennung ----------

    def _detect_devices(self):
        exe = self.exe_path()
        if not exe.is_file():
            self.gpu_var.set(tr("llama-server.exe nicht gefunden: {exe}", exe=exe))
            self._style_gpu_banner(warn=True, show_button=False)
            return

        def work():
            try:
                run = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, errors="replace",
                                     encoding="utf-8", timeout=30, cwd=exe.parent,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, errors="replace",
                                         encoding="utf-8", timeout=30, cwd=exe.parent,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as err:
                self.events.put(lambda message=tr("Geräteprüfung fehlgeschlagen: {err}", err=err): self.gpu_var.set(message))
                if self.sweep_pending:
                    self.events.put(self._begin_sweep)
                if self.optimize_pending:
                    self.events.put(self._begin_optimizer)
                return
            devices = [line.strip() for line in (run.stdout + run.stderr).splitlines()
                       if re.match(r"\s+\S+:", line)]
            build = re.search(r"build (\d+)", version.stdout + version.stderr)
            self.events.put(lambda: self._set_devices(devices, build.group(1) if build else ""))

        threading.Thread(target=work, daemon=True).start()

    def _set_devices(self, devices, build):
        self.build_number = build
        free = [int(value) for device in devices for value in re.findall(r"(\d+) MiB free", device)]
        self.vram_free = free[0] if free else None
        self._schedule_preview()
        if self.sweep_pending:  # Der Kontext-Test hat auf diesen frisch gelesenen Wert gewartet.
            self._begin_sweep()
        if self.optimize_pending:
            self._begin_optimizer()
        if devices:
            self.gpu_var.set(tr("GPU: ") + "   |   ".join(devices))
            self._style_gpu_banner(warn=False, show_button=False)
            return
        folder = Path(self.llama_dir.get())
        if (folder / "ggml-cuda.dll").exists() and not list(folder.glob("cublas64_*.dll")):
            self.gpu_var.set(tr("Keine GPU erkannt: Die CUDA-Laufzeit (cublas64_*.dll, cudart64_*.dll) fehlt im "
                             "llama.cpp-Ordner. Der Server läuft nur auf der CPU."))
            self._style_gpu_banner(warn=True, show_button=True)
        else:
            self.gpu_var.set(tr("Keine GPU erkannt. Der Server läuft nur auf der CPU."))
            self._style_gpu_banner(warn=True, show_button=False)

    def _style_gpu_banner(self, warn, show_button):
        color = "#fdecc8" if warn else "#eef3f8"
        self.gpu_frame.configure(background=color)
        self.gpu_label.configure(background=color)
        if show_button:
            self.gpu_button.pack(side="right", padx=4, pady=2)
        else:
            self.gpu_button.pack_forget()

    def _open_release_page(self):
        tag = f"tag/b{self.build_number}" if self.build_number else "latest"
        webbrowser.open(f"https://github.com/ggml-org/llama.cpp/releases/{tag}")

    # ---------- Serverprozess ----------

    def start_server(self):
        if self.proc or self.sweep_cancel or self.sweep_pending or self.optimize_pending or self.optimize_window is not None:
            return
        errors = validate(self.values())
        if not self.exe_path().is_file():
            errors.append(tr("llama-server.exe nicht gefunden: {exe_path}", exe_path=self.exe_path()))
        if not self.selected_model():
            errors.append(tr("Kein Modell ausgewählt."))
        host = client_host(self.vars["host"].get().strip())
        port = self.vars["port"].get().strip() or "8080"
        if not errors and self._port_in_use(host, int(port)):
            errors.append(tr("Port {port} ist bereits belegt. Läuft noch ein anderer Server?", port=port))
        if errors:
            messagebox.showerror(tr("Start nicht möglich"), "\n".join(errors))
            return

        if not self.model_info:  # Die Automatik braucht die Metadaten, auch wenn das Lesen noch läuft.
            try:
                self._set_model_info(self.selected_model(), read_gguf_info(self.selected_model()))
            except (OSError, ValueError, struct.error, OverflowError, MemoryError) as err:
                self._log_app(tr("Metadaten nicht lesbar: {err}", err=err))
        args = self.current_args()
        if not self.vars["port"].get().strip():
            args += ["--port", port]
        try:
            self.proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=self.exe_path().parent,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as err:
            self.proc = None
            messagebox.showerror(tr("Start fehlgeschlagen"), str(err))
            return

        self._save_config()
        self.server_url = f"http://{host}:{port}"
        self.server_ready = False
        self.stop_requested = False
        self.server_ctx = None
        model_id = self.opencode_model_id()
        self.server_settings = {
            "model_id": model_id,
            "name": self.model_info.get("name") or model_id,
            "file": self.selected_model().stem,
            "base_url": f"{self.server_url}/v1",
            "api_key": self.vars["api_key"].get().strip(),
            "ngl": self.vars["ngl"].get().strip() or "auto",
            "spec": resolve_spec_type(self.vars["spec_type"].get(), self.model_info, self.vram_free,
                                      fit_reserve_mib(self.vars["fit_target"].get())).strip() or "none",
            "ubatch": self.vars["ubatch"].get().strip() or "512",
        }
        self._log_app(tr("Start: ") + subprocess.list2cmdline(args))
        self._set_status(tr("Startet…"), "#d9a400")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._watch_health, args=(self.proc, self.server_url, self.vars["api_key"].get()),
                         daemon=True).start()

    @staticmethod
    def _port_in_use(host, port):
        with socket.socket() as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0

    def stop_server(self):
        if self.proc:
            self.stop_requested = True
            if self.speed_cancel:  # Ein laufender Test endet als Abbruch, nicht als Verbindungsfehler.
                self.speed_cancel.set()
            self._set_status(tr("Wird beendet…"), "#d9a400")
            self.proc.terminate()

    def _read_output(self, proc):
        for line in proc.stdout:
            self.events.put(("log", re.sub(r"\x1b\[[0-9;]*m", "", line.rstrip())))
        code = proc.wait()
        self.events.put(lambda: self._on_exit(proc, code))

    def _watch_health(self, proc, url, api_key):
        ready = False
        while proc.poll() is None:
            status, _ = http_get(f"{url}/health")
            if status == 200 and not ready:
                ready = True
                _, body = http_get(f"{url}/props", api_key)
                try:
                    ctx = json.loads(body)["default_generation_settings"]["n_ctx"]
                except (ValueError, KeyError, TypeError):
                    ctx = None
                self.events.put(lambda c=ctx: self._on_ready(proc, c))
            elif status == 503 and not ready:
                self.events.put(lambda: self._on_loading(proc))
            threading.Event().wait(2 if ready else 0.5)

    def _on_loading(self, proc):
        if proc is self.proc:
            self._set_status(tr("Modell wird geladen…"), "#d9a400")

    def _on_ready(self, proc, ctx):
        if proc is not self.proc:
            return
        self.server_ready = True
        self.server_ctx = ctx
        self.web_button.configure(state="normal")
        self._update_speed_buttons()
        suffix = tr("   ·   Kontext {ctx}", ctx=ctx) if ctx else ""
        self._set_status(tr("Bereit   ·   {server_url}{suffix}", server_url=self.server_url, suffix=suffix), "#2e9e4f")
        self.sync_opencode()
        self._tell_chat()

    def _on_exit(self, proc, code):
        if proc is not self.proc:
            return
        failed_to_start = not self.server_ready and not self.stop_requested
        self.proc = None
        self.server_ready = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.web_button.configure(state="disabled")
        if self.speed_cancel:
            self.speed_cancel.set()
        self._update_speed_buttons()
        self._log_app(tr("Server beendet (Exit-Code {code}).", code=code))
        if failed_to_start:
            self._log_app(tr("Der Server hat vor dem Bereit-Zustand aufgehört, die Ursache steht in den Zeilen darüber."))
            if "draft" in self.server_settings.get("spec", ""):
                self._log_app(tr("Es war {spec} aktiv. Falls es daran liegt: Speculative auf ngram-mod oder none stellen.", spec=self.server_settings['spec']))
        self._set_status(tr("Gestoppt"), "#888888")
        self._tell_chat()
        self._detect_devices()  # Der VRAM ist wieder frei, die Vorschläge rechnen mit dem neuen Wert.

    def _set_status(self, text, color):
        self.status_var.set(text)
        self.status_dot.configure(fg=color)

    def open_webui(self):
        webbrowser.open(self.server_url)

    def open_chat(self):
        if self.chat_window and self.chat_window.winfo_exists():
            self.chat_window.deiconify()
            self.chat_window.lift()
            self.chat_window.input.focus_set()
            return
        self.chat_window = ChatWindow(self, self.config_path.with_name("llama_chats.json"))
        self._tell_chat()

    def _tell_chat(self):
        if self.chat_window and self.chat_window.winfo_exists():
            self.chat_window.set_server(self.server_url if self.server_ready else "", self.server_settings.get("api_key", ""))

    # ---------- OpenCode ----------

    def opencode_model_id(self):
        alias = self.vars["alias"].get().split(",")[0].strip()
        model = self.selected_model()
        return alias or (model.stem if model else "")

    def _effective_context(self):
        """Echter Kontext des laufenden Servers, sonst Formularwert, sonst Modellwert."""
        if self.server_ctx:
            return self.server_ctx
        ctx = self.vars["ctx_size"].get().strip()
        if ctx.isdigit() and int(ctx) > 0:
            return int(ctx)
        return self.model_info.get("context") or 32768

    def sync_opencode(self):
        """Hält den Eintrag des laufenden Servers in der OpenCode-Konfiguration aktuell."""
        path = OPENCODE_CONFIG
        settings = self.server_settings
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            merged = merge_opencode_config(existing, settings["base_url"], settings["model_id"], settings["name"],
                                           self._effective_context(), settings["api_key"])
            if merged == existing:
                self._log_app(tr("OpenCode: {opencode_provider}/{model_id} ist bereits eingetragen.", opencode_provider=OPENCODE_PROVIDER, model_id=settings['model_id']))
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temp.replace(path)
        except (OSError, ValueError) as err:
            self._log_app(tr("OpenCode-Konfiguration nicht geändert: {path}: {err}", path=path, err=err))
            return
        self._log_app(tr("OpenCode: {opencode_provider}/{model_id} eingetragen in {path}", opencode_provider=OPENCODE_PROVIDER, model_id=settings['model_id'], path=path))
        threading.Thread(target=self._reload_opencode, daemon=True).start()

    def _reload_opencode(self):
        """Ein bereits offenes OpenCode Desktop übernimmt die Änderung ohne Neustart."""
        cli = OPENCODE_DESKTOP_CLI
        try:
            tasks = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OpenCode.exe", "/NH"], capture_output=True,
                                   text=True, errors="replace", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            if not cli.is_file() or "OpenCode.exe" not in tasks.stdout:
                return
            run = subprocess.run([str(cli), "reload"], capture_output=True, timeout=30,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            message = tr("OpenCode Desktop hat die Konfiguration neu geladen.") if run.returncode == 0 else \
                tr("OpenCode Desktop konnte nicht neu laden. Bitte die App neu öffnen.")
        except (OSError, subprocess.TimeoutExpired) as err:
            message = tr("OpenCode Desktop konnte nicht neu laden: {err}", err=err)
        self.events.put(lambda: self._log_app(message))

    # ---------- VRAM-Prüfung ----------

    def show_vram_check(self):
        """Zeigt, wer den VRAM belegt. Das Fenster liest nur und beendet selbst keine Programme."""
        if self.vram_window is None or not self.vram_window.winfo_exists():
            self.vram_window = window = tk.Toplevel(self)
            window.title(tr("VRAM-Prüfung"))
            window.geometry(f"760x620+{self.winfo_rootx() + 60}+{self.winfo_rooty() + 60}")
            window.minsize(620, 460)
            window.transient(self)
            window.bind("<Escape>", lambda _: window.destroy())
            frame = ttk.Frame(window, padding=12)
            frame.pack(fill="both", expand=True)
            self.vram_summary = tk.StringVar()
            self.vram_verdict = tk.StringVar()
            self.vram_note = tk.StringVar()
            ttk.Label(frame, textvariable=self.vram_summary, style="Head.TLabel").pack(anchor="w")
            ttk.Label(frame, textvariable=self.vram_verdict, wraplength=720).pack(anchor="w", pady=(6, 8))
            bottom = ttk.Frame(frame)
            bottom.pack(side="bottom", fill="x", pady=(8, 0))
            self.vram_refresh = ttk.Button(bottom, text=tr("Neu prüfen"), command=self.refresh_vram_check)
            self.vram_refresh.pack(side="left")
            self.vram_delta = tk.StringVar()
            ttk.Label(bottom, textvariable=self.vram_delta).pack(side="left", padx=(10, 0))
            ttk.Label(frame, textvariable=self.vram_note, style="Hint.TLabel", wraplength=720).pack(
                side="bottom", anchor="w", pady=(8, 0))
            table = ttk.Frame(frame)
            table.pack(fill="both", expand=True)
            columns = {"name": (tr("Programm"), 190, "w"), "mib": (tr("Höchstens (MiB)"), 110, "e"), "kind": (tr("Was geht"), 400, "w")}
            self.vram_table = ttk.Treeview(table, columns=list(columns), show="headings")
            for key, (title, width, anchor) in columns.items():
                self.vram_table.heading(key, text=title, anchor=anchor)
                self.vram_table.column(key, width=width, anchor=anchor, stretch=key == "kind")
            bar = ttk.Scrollbar(table, orient="vertical", command=self.vram_table.yview)
            self.vram_table.configure(yscrollcommand=bar.set)
            self.vram_table.pack(side="left", fill="both", expand=True)
            bar.pack(side="right", fill="y")
            self.vram_first_free = None
        self.vram_window.deiconify()
        self.vram_window.lift()
        self.refresh_vram_check()

    def refresh_vram_check(self):
        self.vram_refresh.configure(state="disabled")
        self.vram_summary.set(tr("Wird geprüft…"))
        own_pid = self.proc.pid if self.proc else None

        def work():
            try:
                result = summarize_vram(read_vram_snapshot(), own_pid)
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as err:
                self.events.put(lambda message=str(err): self._on_vram_error(message))
                return
            self.events.put(lambda: self._on_vram_result(result))

        threading.Thread(target=work, daemon=True).start()

    def _on_vram_error(self, message):
        if self.vram_window is None or not self.vram_window.winfo_exists():
            return
        self.vram_refresh.configure(state="normal")
        self.vram_summary.set(tr("Die VRAM-Zähler konnten nicht gelesen werden."))
        self.vram_verdict.set(message)

    def _on_vram_result(self, result):
        if self.vram_window is None or not self.vram_window.winfo_exists():
            return
        self.vram_refresh.configure(state="normal")
        if result["total"]:
            self.vram_summary.set(tr("Gesamt {total} MiB   ·   Belegt {used} MiB   ·   Vom Treiber reserviert {reserved} MiB   ·   Frei "
                "{free} MiB", total=result['total'], used=result['used'], reserved=result['reserved'], free=result['free']))
        else:
            self.vram_summary.set(tr("Belegt {used:.0f} MiB", used=result['used']))
        self.vram_verdict.set(self._vram_verdict(result))
        self.vram_table.delete(*self.vram_table.get_children())
        for row in result["rows"]:
            self.vram_table.insert("", "end", values=(row["name"], f"{row['mib']:.0f}", VRAM_KIND_TEXT[row["kind"]]))
        self.vram_note.set(
            tr("Die Werte pro Programm sind Obergrenzen: Windows zählt dort auch Speicher, der gerade nicht im VRAM liegt, deshalb "
            "kann ihre Summe größer sein als der belegte VRAM. Ein Programm schließt du selbst, dieses Fenster beendet nichts. "
            "Danach zeigt „Neu prüfen“, wie viel wirklich frei wurde."))
        free = result["free"]
        if free is not None:
            if self.vram_first_free is None:
                self.vram_first_free = free
            change = free - self.vram_first_free
            self.vram_delta.set(tr("Seit der ersten Prüfung: {change:+d} MiB frei", change=change) if change else "")
        if not self.proc:  # Die Vorschläge für MoE und MTP rechnen mit dem neuen Wert.
            self._detect_devices()

    def _vram_verdict(self, result):
        freeable = result["freeable"]
        parts = []
        if freeable < 50:
            parts.append(tr("Durch Schließen von Programmen lässt sich hier praktisch nichts freigeben."))
        else:
            parts.append(tr("Durch Schließen der Programme werden höchstens etwa {freeable:.0f} MiB frei.", freeable=freeable))
            suggestion = self._moe_suggestion()
            if suggestion:
                extra = int(freeable // suggestion["layer_mib"])
                parts.append(tr("Das reicht für {extra} weitere Expertenschichten.", extra=extra) if extra
                             else tr("Das ist weniger als eine Expertenschicht ({layer_mib:.0f} MiB).", layer_mib=suggestion['layer_mib']))
        if len(result["gpus"]) == 1:
            parts.append(tr("Der Windows-Desktop läuft über deine einzige Grafikkarte ({gpus}), deshalb bleibt sein Anteil "
                "belegt. Der Task-Manager zeigt ihn meist nicht als Programm an.", gpus=result['gpus'][0]))
        elif len(result["gpus"]) > 1:
            parts.append(tr("Du hast mehrere Grafikkarten. Hängt der Monitor an der anderen Karte, belegt der Desktop diese hier nicht."))
        reserve = fit_reserve_mib(self.vars["fit_target"].get())
        if reserve > FIT_SMALL_RESERVE_MIB and self.vars["fit"].get().strip() != "off":
            parts.append(tr("Zusätzlich hält --fit {reserve} MiB Reserve frei. Das Feld „Fit: freier VRAM“ gibt oft mehr Platz "
                "als das Schließen von Programmen, lässt aber weniger Puffer.", reserve=reserve))
        return " ".join(parts)

    # ---------- Speedtest ----------

    def _update_speed_buttons(self):
        """Ein laufender Test lässt sich abbrechen. Ohne Server ist der Button gesperrt."""
        for button in self.speed_buttons:
            button.configure(text=tr("Abbrechen") if self.speed_cancel else tr("Speedtest starten"),
                             state="normal" if self.server_ready else "disabled")

    def _prompt_test_target(self):
        """Bei kleinem Kontext wird der Prompt gekürzt, sonst lehnt der Server ihn ab."""
        target = PROMPT_TEST_TOKENS
        if self.server_ctx:
            target = min(target, self.server_ctx - PROMPT_TEST_RESERVE)
        return target

    def toggle_speedtest(self):
        if self.speed_cancel:
            self.speed_cancel.set()
            self.speed_status.set(tr("Wird abgebrochen…"))
            return
        if not self.server_ready:
            return
        target = self._prompt_test_target()
        self.notebook.select(self.speed_tab)
        self.speed_cancel = threading.Event()
        self.speed_count = 0
        self.speed_first = None
        self.speed_bar.configure(value=0, maximum=max(target, 1))
        self.speed_status.set(tr("Der Prompt wird vorbereitet…"))
        self.speed_text.configure(state="normal")
        self.speed_text.delete("1.0", "end")
        self.speed_text.configure(state="disabled")
        self._update_speed_buttons()
        threading.Thread(target=self._speedtest_worker, args=(
            target, self.server_url, dict(self.server_settings), self.server_ctx, self.speed_cancel),
            daemon=True).start()

    def _speedtest_worker(self, target, url, settings, ctx, cancel):
        status, body = http_get(f"{url}/slots", settings["api_key"], timeout=5)
        try:
            # Während eines Rechenschritts antwortet der Server nicht, das zählt ebenfalls als beschäftigt.
            busy = status is None or (status == 200 and any(slot.get("is_processing") for slot in json.loads(body)))
        except (ValueError, AttributeError):
            busy = False
        if busy:
            self.events.put(lambda: self._on_speedtest_done(error=tr("Der Server bearbeitet gerade eine andere Anfrage.")))
            return
        try:
            prompt = None
            if target >= PROMPT_TEST_MIN_TOKENS:
                prompt = run_prompt_test(url, settings["model_id"], settings["api_key"], target,
                                         lambda progress: self.events.put(lambda: self._on_prompt_progress(progress)), cancel)
                if prompt is None:
                    self.events.put(lambda: self._on_speedtest_done(error=tr("Abgebrochen.")))
                    return
            self.events.put(self._on_answer_phase)
            result = run_speedtest(url, settings["model_id"], settings["api_key"],
                                   lambda piece: self.events.put(lambda: self._on_speed_token(piece)), cancel, SWEEP_ANSWER_SECONDS)
        except (OSError, ValueError, http.client.HTTPException) as err:
            message = tr("Abgebrochen.") if cancel.is_set() else tr("Fehlgeschlagen: {err}", err=err)
            self.events.put(lambda: self._on_speedtest_done(error=message))
            return
        self.events.put(lambda: self._on_speedtest_done(result, prompt, settings, ctx))

    def _on_prompt_progress(self, progress):
        if self.speed_cancel is None or self.speed_cancel.is_set():
            return
        processed = progress.get("processed", 0) - progress.get("cache", 0)
        total = progress.get("total", 0)
        self.speed_bar.configure(value=processed, maximum=total or self.speed_bar["maximum"])
        seconds = progress.get("time_ms", 0) / 1000
        speed = tr("   ·   ca. {value:.0f} Tok/s", value=processed / seconds) if seconds > 0 and processed else ""
        self.speed_status.set(tr("Einlesen: {processed} von {total} Tokens{speed}", processed=processed, total=total, speed=speed))

    def _on_answer_phase(self):
        if self.speed_cancel is None or self.speed_cancel.is_set():
            return
        self.speed_bar.configure(value=0, maximum=SWEEP_ANSWER_SECONDS)
        self.speed_status.set(tr("Die Antwort wird vorbereitet…"))

    def _on_speed_token(self, piece):
        now = time.perf_counter()
        self.speed_first = self.speed_first or now
        self.speed_count += 1
        elapsed = now - self.speed_first
        self.speed_bar.configure(value=elapsed)
        if elapsed > 0:
            self.speed_status.set(tr("Antwort: {speed_count} Tokens   ·   ca. {value:.1f} Tok/s", speed_count=self.speed_count, value=(self.speed_count - 1) / elapsed))
        at_end = self.speed_text.yview()[1] >= 0.999
        self.speed_text.configure(state="normal")
        self.speed_text.insert("end", piece)
        self.speed_text.configure(state="disabled")
        if at_end:
            self.speed_text.see("end")

    def _on_speedtest_done(self, result=None, prompt=None, settings=None, ctx=None, error=""):
        self.speed_cancel = None
        self._update_speed_buttons()
        if result is None:
            self.speed_status.set(error or tr("Abgebrochen."))
            return
        row = {
            "time": time.strftime(tr("%d.%m. %H:%M")),
            "model": settings["file"],
            "ctx": ctx or "",
            "ngl": settings["ngl"],
            "spec": settings["spec"],
            "ubatch": settings.get("ubatch", ""),
            "prompt_tokens": prompt["tokens"] if prompt else "",
            "prompt_tps": round(prompt["prompt_tps"], 1) if prompt else "",
            "tokens": result["tokens"],
            "tps": round(result["tps"], 1),
            "first": round(result["first_token"], 2),
            "total": round(result["total"] + (prompt["total"] if prompt else 0), 1),
            "draft": f"{100 * result['draft_accepted'] / result['draft_n']:.0f} %" if result["draft_n"] else "",
        }
        summary = tr("Antwort: {tps} Tok/s", tps=row['tps'])
        if prompt:
            summary = tr("Einlesen: {prompt_tps} Tok/s   ·   {summary}", prompt_tps=row['prompt_tps'], summary=summary)
        else:
            summary += tr("   ·   Einlesen nicht gemessen, der Kontext ist zu klein.")
        if result["finish"] == "stop":
            summary += tr("   ·   Das Modell hat vor Ablauf der Messzeit aufgehört, der Wert ist weniger aussagekräftig.")
        history = self.config_data.setdefault("speedtests", [])
        history.append(row)
        del history[:-100]
        self._add_speed_row(row)
        self._save_config()
        self.speed_bar.configure(value=SWEEP_ANSWER_SECONDS, maximum=SWEEP_ANSWER_SECONDS)
        self.speed_status.set(summary)

    def _add_speed_row(self, row):
        values = dict(row)
        if row.get("test") == "Prompt":  # Ältere Einträge haben getrennte Zeilen für Prompt und Antwort.
            values["prompt_tokens"], values["tokens"] = row.get("tokens", ""), ""
        elif "prompt_tokens" not in row:  # Noch ältere Läufe enthalten den ungenauen Wert aus dem kurzen Prompt.
            values.pop("prompt_tps", None)
        item =self.speed_table.insert("", 0, values=[values.get(key, "") for key in self.speed_table["columns"]])
        self.speed_table.see(item)

    def clear_speedtests(self):
        self.config_data["speedtests"] = []
        self.speed_table.delete(*self.speed_table.get_children())
        self._save_config()

    # ---------- Modell optimieren ----------

    def open_optimizer(self):
        if self.optimize_window is not None:
            self.optimize_window.lift()
            return
        if self.sweep_cancel or self.sweep_pending or self.optimize_pending or self.speed_cancel:
            messagebox.showinfo(tr("Modell optimieren"), tr("Es läuft gerade schon ein Test. Warte, bis er fertig ist."))
            return
        if self.selected_model() and not self.model_info:  # Die Metadaten werden vielleicht gerade noch im Hintergrund gelesen.
            try:
                self._set_model_info(self.selected_model(), read_gguf_info(self.selected_model()))
            except (OSError, ValueError, struct.error, OverflowError, MemoryError) as err:
                self._log_app(tr("Metadaten nicht lesbar: {err}", err=err))
        errors = validate({**self.values(), "ctx_size": ""})
        if not self.exe_path().is_file():
            errors.append(tr("llama-server.exe nicht gefunden: {exe_path}", exe_path=self.exe_path()))
        if not self.selected_model() or not self.model_info:
            errors.append(tr("Kein lesbares Modell ausgewählt."))
        if errors:
            messagebox.showerror(tr("Modell optimieren nicht möglich"), "\n".join(errors))
            return
        others = other_llama_servers(self.proc.pid if self.proc else None)
        if others and not messagebox.askokcancel(
                tr("Modell optimieren"), tr("Es laufen noch {others} andere llama-server. Sie belegen VRAM und verfälschen die Messung. "
                    "Trotzdem starten?", others=others)):
            return
        self.optimize_resume = None
        last = self._last_optimizer_run(self.selected_model())
        if last:
            answer = messagebox.askyesnocancel(tr("Modell optimieren"), tr(
                "Für dieses Modell gibt es einen gespeicherten Lauf vom {time}. Fortsetzen?\n\nJa: Die gemessenen Schritte bleiben, es geht beim "
                "ersten offenen Schritt weiter.\nNein: Neu beginnen.", time=last[1].get("time", "")))
            if answer is None:
                return
            self.optimize_resume = last if answer else None
        self.optimize_restart = bool(self.proc)
        if self.proc:
            if not messagebox.askokcancel(tr("Modell optimieren"), tr("Der laufende Server wird für die Messungen beendet und danach wieder gestartet.")):
                return
            self.optimize_pending = True
            self.optimize_button.configure(state="disabled")
            self.stop_server()  # Sobald er weg ist und der VRAM neu gelesen wurde, geht es in _set_devices weiter.
            return
        self._begin_optimizer()

    def _begin_optimizer(self):
        self.optimize_pending = False
        model = self.selected_model()
        values = self.values()
        values["spec_type"] = resolve_spec_type(values["spec_type"], self.model_info, self.vram_free, fit_reserve_mib(values["fit_target"]))
        resume = getattr(self, "optimize_resume", None)
        self.optimize_resume = None
        folder = resume[0] if resume else self._optimizer_folder(model) / f"{self._optimizer_prefix(model)}{time.strftime('%Y-%m-%d_%H-%M-%S')}"
        self.optimize_button.configure(state="disabled")
        self.sweep_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self._set_status(tr("Modell wird optimiert"), "#d9a400")
        self._log_app(tr("Modell optimieren: {name}. Diagramme und Messwerte: {folder}", name=model.name, folder=folder))
        self.optimize_window = OptimizeWindow(self, self.exe_path(), model, dict(self.model_info), values, self.vram_free, folder, resume=resume[1] if resume else None)

    def _optimizer_folder(self, model):
        return self.config_path.parent / SWEEP_FOLDER / "optimierung"

    @staticmethod
    def _optimizer_prefix(model):
        return f"{re.sub(r'[^\w.-]+', '_', model.stem)}_"

    def _last_optimizer_run(self, model):
        """Der jüngste gespeicherte Lauf für dieses Modell, wenn sein Kontext-Schritt fertig ist. Sonst lohnt sich das Fortsetzen nicht."""
        if model is None:
            return None
        prefix = self._optimizer_prefix(model)
        folders = sorted((path for path in self._optimizer_folder(model).glob(prefix + "*") if path.is_dir() and path.name[len(prefix):][:1].isdigit()),
                         key=lambda path: path.name, reverse=True)
        for folder in folders[:1]:
            try:
                report = json.loads((folder / "ergebnis.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            context = next((step for step in report.get("steps", []) if step.get("key") == "context"), None)
            if report.get("model") == model.stem and context and context.get("done") and context.get("points"):
                return folder, report
        return None

    def on_optimizer_closed(self):
        self.optimize_window = None
        self.optimize_button.configure(state="normal")
        self.sweep_button.configure(state="normal")
        self.start_button.configure(state="normal")
        self._set_status(tr("Gestoppt"), "#888888")
        self._detect_devices()
        if self.optimize_restart:
            self.optimize_restart = False
            self.start_server()

    def optimize_limit(self, values):
        return self._sweep_limit(values)

    def optimize_range_default(self, values):
        """Vorschlag für den Kontext-Bereich: (von, bis, VRAM-Grenze laut Schätzung, Maximum des Modells)."""
        limit = self._sweep_limit(values)
        trained = self.model_info.get("context")
        low = min(SWEEP_GRID[0], trained) if trained else SWEEP_GRID[0]
        return low, sweep_contexts(limit, trained)[-1], limit, trained

    def optimize_moe_suggestion(self, values, context, free):
        if free is None:
            return None
        return moe_suggestion(self.model_info, free, context, fit_reserve_mib(values.get("fit_target", "")), values.get("cache_k", "").strip(), values.get("cache_v", "").strip())

    @staticmethod
    def optimize_describe(values):
        shown = build_args("", "", {key: value for key, value in values.items() if key not in ("ctx_size", "host", "port", "api_key")})
        return subprocess.list2cmdline(shown[1:])

    @staticmethod
    def optimize_command(exe, model, values):
        return subprocess.list2cmdline(build_args(exe, model, values))

    def profile_exists(self, name):
        return name in self.config_data.get("profiles", {})

    def save_optimized_profile(self, name, values, model):
        data = dict(values)
        data["_model"] = str(model)
        self.config_data.setdefault("profiles", {})[name] = data
        self._load_values(data)
        self.refresh_models(str(model))
        self.profile_var.set(name)
        self._save_config()
        self._refresh_profiles()

    # ---------- Kontext-Test ----------

    def _sweep_limit(self, values=None):
        """Kontext, bis zu dem laut Schätzung alles in den VRAM passt."""
        estimate = self._vram_estimate(values)
        return estimate["max_context"] if estimate and not estimate["no_gpu"] else None

    def _sweep_plan(self):
        """Liefert die Messpunkte aus dem Feld oder automatisch. Wirft ValueError bei falscher Eingabe."""
        text = self.sweep_contexts_var.get().strip()
        if text:
            return parse_contexts(text)
        return sweep_contexts(self._sweep_limit(), self.model_info.get("context"))

    def _update_sweep_hint(self):
        if not self.model_info:
            self.sweep_hint.set("")
        elif self.sweep_contexts_var.get().strip():
            self.sweep_hint.set(tr("Eigene Liste, zum Beispiel: 16384, 32k, 40000"))
        else:
            plan = sweep_contexts(self._sweep_limit(), self.model_info.get("context"))
            self.sweep_hint.set(tr("Automatisch: ") + ", ".join(llama_plot.compact_tokens(value) for value in plan))

    def toggle_sweep(self):
        if self.optimize_window is not None or self.optimize_pending:
            return
        if self.sweep_cancel:
            self.sweep_cancel.set()
            self.sweep_status.set(tr("Wird abgebrochen…"))
            if self.sweep_proc and self.sweep_proc.poll() is None:
                self.sweep_proc.terminate()
            return
        if self.sweep_pending:
            return
        errors = validate({**self.values(), "ctx_size": ""})
        if not self.exe_path().is_file():
            errors.append(tr("llama-server.exe nicht gefunden: {exe_path}", exe_path=self.exe_path()))
        if not self.selected_model() or not self.model_info:
            errors.append(tr("Kein lesbares Modell ausgewählt."))
        if not errors:
            try:
                self._sweep_plan()
            except ValueError as err:
                errors.append(str(err))
        if errors:
            messagebox.showerror(tr("Kontext-Test nicht möglich"), "\n".join(errors))
            return
        own_pid = self.proc.pid if self.proc else None
        others = other_llama_servers(own_pid)
        if others and not messagebox.askokcancel(
                tr("Kontext-Test"), tr("Es laufen noch {others} andere llama-server. Sie belegen VRAM und verfälschen die Messung. "
                    "Trotzdem starten?", others=others)):
            return
        self.sweep_restart = bool(self.proc)
        if self.proc:
            if not messagebox.askokcancel(tr("Kontext-Test"), tr("Der laufende Server wird für den Test beendet und danach wieder gestartet.")):
                return
            self.sweep_pending = True
            self.sweep_button.configure(state="disabled")
            self.sweep_status.set(tr("Der laufende Server wird beendet…"))
            self.stop_server()  # Sobald er weg ist und der VRAM neu gelesen wurde, geht es in _set_devices weiter.
            return
        self._begin_sweep()

    def _begin_sweep(self):
        self.sweep_pending = False
        contexts = [c for c in self._sweep_plan() if not self.model_info["context"] or c <= self.model_info["context"]]
        if not contexts:
            self.sweep_button.configure(state="normal")
            self.sweep_status.set(tr("Alle Kontexte liegen über dem Maximum dieses Modells."))
            if self.sweep_restart:
                self.sweep_restart = False
                self.start_server()
            return
        values = self.values()
        values["spec_type"] = resolve_spec_type(values["spec_type"], self.model_info, self.vram_free, fit_reserve_mib(values["fit_target"]))
        shown = build_args("", "", {key: value for key, value in values.items() if key not in ("ctx_size", "host", "port", "api_key")})
        model = self.selected_model()
        stamp = time.strftime(tr("%d.%m.%Y %H:%M"))
        self.sweep_current = {
            "time": stamp,
            "model": model.stem,
            "settings": subprocess.list2cmdline(shown[1:]),
            "fit_limit": self._sweep_limit(),
            "free": self.vram_free,
            "contexts": contexts,
            "points": [],
            "complete": False,
            "image": str(self._sweep_image_path(model.stem)),
        }
        self.sweep_cancel = threading.Event()
        self.sweep_shown = self.sweep_current
        self.sweep_bar.configure(value=0, maximum=len(contexts))
        self.sweep_button.configure(text=tr("Abbrechen"), state="normal")
        self.start_button.configure(state="disabled")
        self._set_status(tr("Kontext-Test läuft"), "#d9a400")
        self.notebook.select(self.sweep_tab)
        self._fill_sweep_table(self.sweep_current)
        self._draw_sweep()
        self._log_app(tr("Kontext-Test: {count} Messpunkte für {name}.", count=len(contexts), name=model.name))
        threading.Thread(target=self._sweep_worker, args=(self.exe_path(), model, values, contexts, self.sweep_cancel), daemon=True).start()

    def _sweep_image_path(self, stem):
        folder = self.config_path.parent / SWEEP_FOLDER
        safe = re.sub(r"[^\w.-]+", "_", stem)
        return folder / f"{safe}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.png"

    def _sweep_worker(self, exe, model, values, contexts, cancel):
        for index, requested in enumerate(contexts):
            if cancel.is_set():
                break
            started = time.perf_counter()
            announce = lambda phase, i=index, c=requested: self.events.put(lambda: self._on_sweep_phase(i, c, phase))
            point = self._measure_context(exe, model, values, requested, cancel, announce)
            if cancel.is_set():
                break
            point["seconds"] = round(time.perf_counter() - started, 1)
            self.events.put(lambda p=point, done=index + 1: self._on_sweep_point(p, done))
        self.events.put(lambda: self._on_sweep_end(cancel.is_set()))

    def _measure_context(self, exe, model, values, requested, cancel, announce, repeat=False):
        """Startet einen eigenen Server mit dieser Kontextgröße, misst beide Werte und beendet ihn wieder. repeat misst zusätzlich wiederholten Text."""
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        api_key = values["api_key"].strip()
        args = build_args(exe, model, {**values, "ctx_size": str(requested), "host": "127.0.0.1", "port": str(port)})
        point = {"requested": requested, "ctx": requested, "state": tr("Fehler")}
        tail = deque(maxlen=12)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, cwd=exe.parent, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as err:
            point["error"] = str(err)
            return point
        self.sweep_proc = proc
        threading.Thread(target=lambda: [tail.append(line.rstrip()) for line in proc.stdout], daemon=True).start()
        try:
            announce(tr("Modell wird geladen"))
            started = time.perf_counter()
            while http_get(f"{url}/health")[0] != 200:
                if cancel.is_set():
                    return point
                if proc.poll() is not None or time.perf_counter() - started > SWEEP_LOAD_TIMEOUT:
                    point["error"] = next((l for l in reversed(tail) if " E " in l or "error" in l.lower()), tr("Der Server ist nicht gestartet."))[:300]
                    return point
                time.sleep(0.3)
            _, body = http_get(f"{url}/props", api_key, timeout=10)
            try:
                point["ctx"] = json.loads(body)["default_generation_settings"]["n_ctx"]
            except (ValueError, KeyError, TypeError):
                pass
            target = min(PROMPT_TEST_TOKENS, point["ctx"] - PROMPT_TEST_RESERVE)
            if target >= PROMPT_TEST_MIN_TOKENS:
                announce(tr("Prompt wird eingelesen"))
                prompt = run_prompt_test(url, "sweep", api_key, target, lambda progress: None, cancel)
                if prompt is None:
                    return point
                point["prompt_tps"] = round(prompt["prompt_tps"], 1)
                point["prompt_tokens"] = prompt["tokens"]
            point["vram"] = vram_used_mib()
            announce(tr("Antwort wird {sweep_answer_seconds} s lang gemessen", sweep_answer_seconds=SWEEP_ANSWER_SECONDS))
            answer = run_speedtest(url, "sweep", api_key, lambda piece: None, cancel, SWEEP_ANSWER_SECONDS)
            if answer is None:
                return point
            point.update(tps=round(answer["tps"], 1), tokens=answer["tokens"], state="OK")
            if answer["draft_n"]:
                point["draft"] = round(100 * answer["draft_accepted"] / answer["draft_n"])
            if repeat:
                announce(tr("Wiederholter Text wird {sweep_answer_seconds} s lang gemessen", sweep_answer_seconds=SWEEP_ANSWER_SECONDS))
                copied = run_repeat_test(url, "sweep", api_key, min(REPEAT_TEST_TOKENS, (point["ctx"] - PROMPT_TEST_RESERVE) // 2), cancel, SWEEP_ANSWER_SECONDS)
                if copied is None:
                    return point
                point["repeat_tps"] = round(copied["tps"], 1)
                if copied["draft_n"]:
                    point["repeat_draft"] = round(100 * copied["draft_accepted"] / copied["draft_n"])
        except (OSError, ValueError, http.client.HTTPException) as err:
            point["error"] = str(err)[:300]
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            self.sweep_proc = None
            time.sleep(SWEEP_SETTLE_SECONDS)  # Bis der VRAM wirklich frei ist, damit der nächste Punkt nicht darunter leidet.
        return point

    def _on_sweep_phase(self, index, context, phase):
        if not self.sweep_current or not self.sweep_cancel or self.sweep_cancel.is_set():
            return
        total = len(self.sweep_current["contexts"])
        done = [p["seconds"] for p in self.sweep_current["points"]]
        remaining = ""
        if done:
            minutes = sum(done) / len(done) * (total - index) / 60
            remaining = tr("   ·   noch etwa {value} Min.", value=max(1, round(minutes)))
        self.sweep_status.set(tr("Punkt {value} von {total}: {context} Kontext, {phase}{remaining}", value=index + 1, total=total, context=llama_plot.compact_tokens(context), phase=phase, remaining=remaining))

    def _on_sweep_point(self, point, done):
        """Jeder Punkt wird sofort gesichert, damit ein Abbruch oder Absturz nichts kostet."""
        sweep = self.sweep_current
        if not sweep:
            return
        sweep["points"].append(point)
        self.sweep_bar.configure(value=done)
        if point.get("error"):
            self._log_app(tr("Kontext {requested}: {error}", requested=point['requested'], error=point['error']))
        self._store_sweep(sweep)
        if self.sweep_shown is sweep:
            self._fill_sweep_table(sweep)
            self._draw_sweep()

    def _on_sweep_end(self, cancelled):
        sweep = self.sweep_current
        self.sweep_current = None
        self.sweep_cancel = None
        self.sweep_button.configure(text=tr("Kontext-Test starten"), state="normal")
        self.start_button.configure(state="normal")
        self._set_status(tr("Gestoppt"), "#888888")
        if sweep:
            sweep["complete"] = len(sweep["points"]) == len(sweep["contexts"])
            if sweep["points"]:
                self._store_sweep(sweep)
                if self.sweep_shown is sweep:
                    self._draw_sweep()
                measured = sum(1 for p in sweep["points"] if p["state"] == "OK")
                prefix = tr("Abgebrochen") if cancelled else tr("Fertig")
                self.sweep_status.set(tr("{prefix}: {measured} von {count} Punkten gemessen. Gespeichert als {name}", prefix=prefix, measured=measured, count=len(sweep['contexts']), name=Path(sweep['image']).name))
                self._log_app(tr("Kontext-Test gespeichert: {image}", image=sweep['image']))
            else:
                self.sweep_shown = self._saved_sweeps()[-1] if self._saved_sweeps() else None
                self._refresh_saved_sweeps()
                self.sweep_status.set(tr("Abgebrochen, bevor ein Punkt fertig war."))
                self._fill_sweep_table(self.sweep_shown)
                self._draw_sweep()
        self._detect_devices()
        if self.sweep_restart:
            self.sweep_restart = False
            self.start_server()

    def _saved_sweeps(self):
        return self.config_data.setdefault("context_tests", [])

    def _store_sweep(self, sweep):
        saved = self._saved_sweeps()
        if sweep not in saved:
            saved.append(sweep)
            del saved[:-SWEEP_KEEP]
        try:
            self._save_config()
        except OSError as err:
            self._log_app(tr("Konfiguration nicht gespeichert: {err}", err=err))
        self._save_sweep_image(sweep)
        self._refresh_saved_sweeps()

    def _save_sweep_image(self, sweep):
        try:
            image = Path(sweep["image"])
            image.parent.mkdir(parents=True, exist_ok=True)
            llama_plot.save_png(sweep, image)
        except ImportError:
            self._log_app(tr("Für das Bild fehlt matplotlib: py -m pip install matplotlib. Die Messwerte sind trotzdem gespeichert."))
        except (OSError, ValueError) as err:
            self._log_app(tr("Bild nicht gespeichert: {err}", err=err))

    @staticmethod
    def _sweep_label(sweep):
        state = "" if sweep.get("complete") else tr("   (unvollständig)")
        return tr("{time}   ·   {model}   ·   {count} Punkte{state}", time=sweep['time'], model=sweep['model'], count=len(sweep['points']), state=state)

    def _refresh_saved_sweeps(self):
        saved = self._saved_sweeps()
        labels = [self._sweep_label(sweep) for sweep in reversed(saved)]
        self.sweep_box.configure(values=labels)
        if self.sweep_shown is None and saved:
            self.sweep_shown = saved[-1]
        self.sweep_choice.set(self._sweep_label(self.sweep_shown) if self.sweep_shown in saved else "")
        self._fill_sweep_table(self.sweep_shown)

    def _show_saved_sweep(self):
        index = self.sweep_box.current()
        saved = self._saved_sweeps()
        if 0 <= index < len(saved):
            self.sweep_shown = saved[len(saved) - 1 - index]
            self._fill_sweep_table(self.sweep_shown)
            self._draw_sweep()

    def _fill_sweep_table(self, sweep):
        self.sweep_table.delete(*self.sweep_table.get_children())
        for point in (sweep or {}).get("points", []):
            self.sweep_table.insert("", "end", values=(
                point.get("ctx", point.get("requested", "")), point.get("prompt_tps", ""), point.get("tps", ""),
                point.get("vram") or "", point.get("state", "")))

    def open_sweep_image(self):
        image = Path((self.sweep_shown or {}).get("image", ""))
        if self.sweep_shown:  # Neu zeichnen, damit auch ältere Tests im aktuellen Stil erscheinen.
            self._save_sweep_image(self.sweep_shown)
        if not self.sweep_shown or not image.is_file():
            messagebox.showinfo(tr("Kontext-Test"), tr("Für diese Auswahl gibt es noch kein Bild."))
            return
        os.startfile(image)

    def delete_sweep(self):
        sweep = self.sweep_shown
        if not sweep or sweep is self.sweep_current or sweep not in self._saved_sweeps():
            return
        if not messagebox.askokcancel(tr("Kontext-Test löschen"), tr("{sweep_label}\n\nMesswerte und Bild werden gelöscht.", sweep_label=self._sweep_label(sweep))):
            return
        self._saved_sweeps().remove(sweep)
        try:
            Path(sweep["image"]).unlink(missing_ok=True)
        except OSError as err:
            self._log_app(tr("Bild nicht gelöscht: {err}", err=err))
        self._save_config()
        self.sweep_shown = None
        self._refresh_saved_sweeps()
        self._draw_sweep()

    def _ensure_sweep_canvas(self):
        if self.sweep_canvas or self.sweep_figure is False:
            return
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            self.sweep_figure = False
            ttk.Label(self.sweep_plot, justify="left", text=(
                tr("Für den Plot fehlt matplotlib. Die Messung läuft trotzdem, und die Werte stehen in der Tabelle.\n"
                "Installieren mit: py -m pip install matplotlib"))).pack(anchor="nw")
            return
        self.sweep_figure = Figure(layout="constrained", dpi=96)
        self.sweep_canvas = FigureCanvasTkAgg(self.sweep_figure, master=self.sweep_plot)
        self.sweep_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.sweep_canvas.mpl_connect("motion_notify_event", self._on_sweep_hover)
        self.sweep_canvas.mpl_connect("figure_leave_event", lambda _: self._hide_sweep_hover())

    def _draw_sweep(self):
        if not self.sweep_canvas:
            return
        sweep = self.sweep_shown or {"points": [], "model": tr("Noch kein Test gespeichert"), "time": "", "complete": True}
        self.sweep_axes = llama_plot.draw_sweep(self.sweep_figure, sweep, compact=True)
        self.sweep_hover = None
        self.sweep_canvas.draw_idle()

    def _hide_sweep_hover(self):
        if self.sweep_hover:
            for artist in self.sweep_hover:
                artist.remove()
            self.sweep_hover = None
            self.sweep_canvas.draw_idle()

    def _on_sweep_hover(self, event):
        """Zeigt am nächstliegenden Messpunkt die genauen Werte."""
        axes = self.sweep_axes
        if not axes or not event.inaxes or event.x is None:
            self._hide_sweep_hover()
            return
        top = axes["prompt"]
        measured = [p for p in axes["points"] if p.get("ctx")]
        if not measured:
            return
        pixel = lambda point: top.transData.transform((point["ctx"], 1))[0]
        nearest = min(measured, key=lambda point: abs(pixel(point) - event.x))
        self._hide_sweep_hover()
        lines = [tr("Kontext {ctx}", ctx=llama_plot.format_number(nearest['ctx'], 0))]
        if nearest.get("prompt_tps"):
            lines.append(tr("Prompt {prompt_tps} Tok/s", prompt_tps=llama_plot.format_number(nearest['prompt_tps'])))
        if nearest.get("tps"):
            lines.append(tr("Antwort {tps} Tok/s", tps=llama_plot.format_number(nearest['tps'])))
        if nearest.get("vram"):
            lines.append(tr("VRAM {vram} MiB", vram=llama_plot.format_number(nearest['vram'], 0)))
        if nearest.get("error"):
            lines.append(nearest["error"][:60])
        guides = [axis.axvline(nearest["ctx"], color=llama_plot.TEXT_SECONDARY, linewidth=1, zorder=2) for axis in (top, axes["answer"])]
        # Die Beschriftung wechselt rechts von der Mitte auf die linke Seite, damit sie nicht aus dem Bild ragt.
        left_side = pixel(nearest) > (top.bbox.x0 + top.bbox.x1) / 2
        label = top.annotate(
            "\n".join(lines), xy=(nearest["ctx"], 0.92), xycoords=("data", "axes fraction"), xytext=(-10 if left_side else 10, 0),
            textcoords="offset points", ha="right" if left_side else "left", va="top", fontsize=9, color=llama_plot.TEXT_PRIMARY,
            bbox={"boxstyle": "round,pad=0.4", "facecolor": llama_plot.SURFACE, "edgecolor": llama_plot.GRID}, zorder=10)
        self.sweep_hover = guides + [label]
        self.sweep_canvas.draw_idle()

    # ---------- Log und Ereignisse ----------

    def _pump(self):
        # Auch nach einem Fehler in einem Ereignis muss die Schleife weiterlaufen.
        self._pump_id = self.after(100, self._pump)
        lines = []
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    lines.append(event[1])
                else:
                    self._append_log(lines)
                    lines = []
                    event()
        except queue.Empty:
            pass
        self._append_log(lines)

    def _append_log(self, lines, tag=None):
        if not lines:
            return
        at_end = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        for line in lines:
            line_tag = tag
            if tag is None:
                if re.search(r"\s(E|error:)\s|^error", line):
                    line_tag = "error"
                elif re.search(r"^\S+\sW\s", line):
                    line_tag = "warn"
            self.log.insert("end", line + "\n", line_tag or ())
        excess = int(self.log.index("end-1c").split(".")[0]) - 5000
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")

    def _log_app(self, text):
        self._append_log([text], "app")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def on_close(self):
        if self.speed_cancel:
            self.speed_cancel.set()
        if self.optimize_window is not None:
            if not messagebox.askokcancel(tr("Beenden"), tr("Das Optimieren läuft noch. Fertige Messungen und Diagramme bleiben gespeichert.")):
                return
            self.optimize_restart = False
            self.optimize_window.close(force=True)
        if self.sweep_cancel:
            if not messagebox.askokcancel(tr("Beenden"), tr("Der Kontext-Test läuft noch. Die fertigen Messpunkte bleiben gespeichert.")):
                return
            self.sweep_cancel.set()
            self.sweep_restart = False
            if self.sweep_proc and self.sweep_proc.poll() is None:
                self.sweep_proc.terminate()
        if self.proc:
            if not messagebox.askokcancel(tr("Beenden"), tr("Der Server läuft noch und wird beim Schließen beendet.")):
                return
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._save_config()
        self.after_cancel(self._pump_id)
        self.destroy()
        self.closed = True

    def _change_language(self):
        """Die Sprache steht beim Programmstart fest (siehe llama_lang), deshalb wird sie gespeichert und das Programm neu gestartet."""
        code = next(key for key, name in self.language_names.items() if name == self.language_var.get())
        self.config_data["language"] = code
        self._save_config()
        if code == llama_lang.current:
            return
        question = llama_lang.tr_in(code, "Die Sprache ändert sich beim nächsten Start. Jetzt neu starten?")
        if messagebox.askyesno("NoProbLlama", question, parent=self):
            self.restart()

    def restart(self):
        self.on_close()
        if self.closed:  # Beim Schließen lässt sich noch abbrechen, etwa wegen eines laufenden Tests.
            subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(Path(__file__).parent))


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NoProbLlama")  # Sonst zeigt die Taskleiste das Symbol von Python.
    except (AttributeError, OSError):
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
