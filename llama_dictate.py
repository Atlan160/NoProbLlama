"""Diktieren für das Chatfenster: Aufnahme über Windows (MCI), Erkennung mit whisper.cpp.

Whisper läuft nur für die Dauer der Erkennung. So belegt das Diktieren keinen VRAM, solange niemand spricht.
"""

import ctypes
import os
import subprocess
import tempfile
from pathlib import Path

from llama_lang import tr

DEFAULT_WHISPER_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "whisper.cpp"
DEFAULT_WHISPER_MODEL = Path.home() / "Documents" / "KI Modelle" / "whisper" / "ggml-large-v3-turbo-q8_0.bin"
WHISPER_EXE = "whisper-cli.exe"
WHISPER_TIMEOUT = 300  # Sekunden für eine Erkennung. Lange Diktate brauchen auf der CPU so lange.
SAMPLE_RATE = 16000  # Whisper rechnet mit 16 kHz mono, so muss nichts umgewandelt werden.
ALIAS = "noprobllama_dictation"


def find_whisper(folder):
    """Sucht whisper-cli.exe im Ordner oder eine Ebene tiefer, wie es im Release-Archiv liegt."""
    folder = Path(folder)
    for candidate in (folder / WHISPER_EXE, *sorted(folder.glob(f"*/{WHISPER_EXE}"))):
        if candidate.is_file():
            return candidate
    return None


def _mci(command):
    """Schickt einen MCI-Befehl an Windows. Bei einem Fehler kommt die Meldung von Windows als RuntimeError."""
    winmm = ctypes.windll.winmm
    result = winmm.mciSendStringW(command, None, 0, None)
    if result:
        buffer = ctypes.create_unicode_buffer(256)
        winmm.mciGetErrorStringW(result, buffer, 256)
        raise RuntimeError(buffer.value or tr("Windows meldet Fehler {code} bei der Aufnahme.", code=result))


class Recorder:
    """Nimmt vom Standard-Mikrofon auf. stop() speichert eine WAV-Datei und liefert ihren Pfad."""

    def __init__(self):
        self.active = False

    def start(self):
        self.close()
        _mci(f"open new type waveaudio alias {ALIAS}")
        try:
            _mci(f"set {ALIAS} bitspersample 16 channels 1 samplespersec {SAMPLE_RATE} bytespersec {SAMPLE_RATE * 2} alignment 2")
            _mci(f"record {ALIAS}")
        except RuntimeError:
            self.close()
            raise
        self.active = True

    def stop(self):
        handle, name = tempfile.mkstemp(prefix="noprobllama_", suffix=".wav")
        os.close(handle)
        try:
            _mci(f"stop {ALIAS}")
            _mci(f'save {ALIAS} "{name}"')
        finally:
            self.close()
        return Path(name)

    def close(self):
        try:
            ctypes.windll.winmm.mciSendStringW(f"close {ALIAS}", None, 0, None)
        except (AttributeError, OSError):
            pass
        self.active = False


def transcribe(exe, model, audio, language="auto"):
    """Lässt whisper-cli eine Aufnahme abschreiben. Liefert den Text, wirft RuntimeError mit dem Grund."""
    run = subprocess.run([str(exe), "-m", str(model), "-f", str(audio), "-l", language, "-nt", "-np"], capture_output=True,
                         timeout=WHISPER_TIMEOUT, cwd=str(Path(exe).parent), creationflags=subprocess.CREATE_NO_WINDOW)
    text = run.stdout.decode("utf-8", "replace")
    if run.returncode != 0:
        detail = run.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(detail[-1] if detail else tr("whisper-cli ist mit Code {code} beendet.", code=run.returncode))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(line for line in lines if not (line.startswith("[") and line.endswith("]")))  # [BLANK_AUDIO] und Ähnliches weglassen.
