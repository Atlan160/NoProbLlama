<img src="noprobllama.png" alt="" width="96" align="right">

# NoProbLlama

*No problem, Llama.*

Eine Windows-Oberfläche für [llama.cpp](https://github.com/ggml-org/llama.cpp), die lokale Sprachmodelle aus kleinen Grafikkarten herausholt: passende Parameter finden, den Speicherbedarf vorab abschätzen, das Tempo messen und ohne Browser chatten.

![Hauptfenster mit Modellauswahl, VRAM-Schätzung, Knöpfen und Einstellungen](Bilder/hauptfenster.png)

## Wofür das gut ist

Auf einer Grafikkarte mit 8 bis 16 GB entscheiden oft wenige hundert MiB, ob ein Modell flüssig läuft. Rutscht ein Teil davon in den Arbeitsspeicher, bricht das Tempo stark ein. Gleichzeitig hat `llama-server` rund hundert Startoptionen, und welche davon wirken, hängt vom Modell ab.

NoProbLlama beantwortet dafür vor allem drei Fragen:

- **Passt das Modell mit diesem Kontext in meinen VRAM?** Das steht schon vor dem Start da.
- **Wie viel Tempo kostet mich mehr Kontext?** Das misst der Kontext-Test.
- **Wie bleibt möglichst viel VRAM und Kontext für das Modell übrig?** Dafür gibt es das schlanke Chatfenster.

## Funktionen

### Parameter einstellen und vorschlagen lassen

- **Alle wichtigen Optionen:** 95 Optionen von `llama-server` in 5 Reitern, die wirkungsvollsten stehen oben.
- **Bewertung jeder Option:** Punkte zeigen, wie stark sie auf Tempo, Qualität und Speicher wirken kann. Das `?` daneben erklärt sie. Die Punkte sind Richtwerte, keine Messungen für jedes Modell.
- **Modell verstehen:** Das Programm liest aus der GGUF-Datei Schichten, Experten und Kontextgrößen, ohne das Modell zu laden.
- **Automatische Vorschläge:**
  - Die Spekulationsart (`--spec-type auto`) richtet sich nach dem Modell.
  - Bei MoE-Modellen errechnet es, wie viele Expertenschichten in den VRAM passen, und übernimmt den Vorschlag auf Knopfdruck.
- **Warnungen:** Es meldet zum Beispiel fehlende Modellteile, wirkungslose Optionen, einen Kontext über dem Trainingswert oder einen Server im Netzwerk ohne API-Key.
- **Profile:** Einstellungen speichern und wieder laden. Der fertige Befehl lässt sich jederzeit kopieren.

### Speicherbedarf abschätzen

- **VRAM-Schätzung:** Sie rechnet Modell, Kontext und Zusatz zusammen und vergleicht sie mit dem freien VRAM. Sie meldet auch, bis zu welcher Kontextgröße alles passt. Die Kontextgröße berücksichtigt die eingestellten Datentypen (zum Beispiel `-ctk q8_0`).
- **Kontext-Tabelle:** Für das gewählte Modell steht dort, was verschiedene Kontextgrößen belegen.
- **VRAM-Prüfung:** Sie zeigt, welche Programme den Grafikspeicher gerade belegen und was davon frei werden kann. Sie liest nur und beendet nichts.

Das bleibt eine Schätzung. Sie kann laut den bisherigen Messungen bis zu etwa 300 MiB danebenliegen, bei MoE-Modellen auch mehr.

### Tempo messen

![Kontext-Test: Prompt-Tempo und Antworttempo über sechs Kontextgrößen, mit Messwerten und der geschätzten VRAM-Grenze](Bilder/kontext_test.png)

Das Beispiel zeigt Hermes 4 14B (IQ4_XS) auf einer RTX 4070 mit 12 GB: Bis etwa 26.600 Tokens Kontext bleibt das Antworttempo bei rund 58 Tok/s. Darüber wird der VRAM knapp, und bei 40.960 Tokens sind es nur noch 29,2 Tok/s.

- **Speedtest:** Er misst am laufenden Server das Tempo beim Antworten und beim Einlesen eines Prompts von etwa 8.000 Tokens. Die Ergebnisse bleiben im Verlauf.
- **Kontext-Test:** Er startet den Server für jede gewählte Kontextgröße neu und misst beide Werte. Die Antwort wird nach 10 Sekunden abgeschnitten, weil sich das Tempo bis dahin eingependelt hat. Das Ergebnis erscheint als zwei Diagramme übereinander und wird als Bild gespeichert. So siehst du, ab welchem Kontext das Tempo einbricht.

### Eigenes Chatfenster

![Chatfenster mit Kontextbalken, eingeklappten Gedanken und Knopf zum Kompaktieren](Bilder/chat.png)

Zum Chatten braucht es weder Browser noch Agenten-Werkzeug. Das Fenster spricht direkt mit der Schnittstelle des Servers und spart dadurch an zwei Stellen:

- **VRAM:** Ein Browser belegt Grafikspeicher, in einer Messung waren es bei Chrome mit allen Tabs 140 bis 205 MiB. Für das Chatfenster meldet Windows 0 MiB. Das lohnt sich vor allem auf kleinen Karten, wenn das Modell knapp passt und der Browser dafür ganz geschlossen wird. Das Antworttempo war mit und ohne Fenster gleich.
- **Kontext:** Agenten-Werkzeuge wie OpenCode oder Open Interpreter schicken bei jeder Nachricht ihre Anweisungen und Werkzeugbeschreibungen mit. Bei OpenCode waren schon mit der ersten Nachricht 7.000 bis 8.000 Tokens belegt, in einem Kontext von 8.192 Tokens bleibt da kaum noch etwas. Im Chatfenster kostet eine kurze erste Frage rund 30 Tokens.

Das Fenster kann außerdem:

- Antworten live streamen, mit Markdown, Tabellen und Codeblöcken samt Kopieren-Link
- Formeln lesbar darstellen (als Unicode-Text, kein echter Formelsatz)
- das Nachdenken in Stufen einstellen und die Gedanken eingeklappt zeigen
- Bilder und Textdateien anhängen, wenn das Modell Bilder lesen kann
- Nachrichten bearbeiten, Antworten neu erzeugen, einen System-Prompt setzen und die Schriftgröße ändern
- das Eingabefeld für längere Texte größer ziehen und darin scrollen
- den belegten Kontext als farbigen Balken mit genauen Zahlen zeigen
- **kompaktieren:** Auf Knopfdruck fasst das Modell den bisherigen Chat zusammen, ein Hinweis erscheint, wenn es sich lohnt. Der alte Verlauf bleibt lesbar, und der Schritt lässt sich rückgängig machen. Eine Zusammenfassung kann Details verlieren oder verfälschen, ein Blick hinein lohnt sich also.

### OpenCode anbinden

Beim Start trägt NoProbLlama den laufenden Server samt Kontextgröße als Anbieter in `~/.config/opencode/opencode.json` ein. Andere Einträge bleiben unverändert, die vorherige Datei wird als `.bak` gesichert. Ein offenes OpenCode Desktop lädt die Änderung neu.

## Voraussetzungen

- **Windows.** Getestet mit Windows 11. Das Programm nutzt Windows-Funktionen und läuft auf anderen Systemen nicht.
- **Python 3 mit Tkinter.** Getestet mit Python 3.13.
- **llama.cpp** als [Windows-Release](https://github.com/ggml-org/llama.cpp/releases), bei NVIDIA-Karten mit CUDA-Laufzeit, bei AMD-Karten zum Beispiel als Vulkan-Build. Getestet mit Build 12054 (CUDA). Neuere Builds können Optionen ändern.
- **Modelle** im GGUF-Format.
- **matplotlib** (optional) für die Diagramme. Ohne das Paket läuft der Kontext-Test trotzdem, die Werte stehen dann in der Tabelle.

```bash
py -m pip install matplotlib
```

Entwickelt und getestet wurde mit einer RTX 4070 mit 12 GB, vor allem mit Hermes 4 und Qwen3.8. Andere NVIDIA-Karten sollten genauso laufen. AMD-Karten sind bisher nicht auf echter Hardware geprüft: Das Programm spricht nur `llama-server` an, ohne `nvidia-smi` fehlen aber Gesamtwerte in der VRAM-Prüfung und VRAM-Werte im Kontext-Test. Bei mehreren Grafikkarten rechnet die Schätzung mit dem ersten gemeldeten Gerät.

## Start

```bash
git clone <Repo-URL>
```

1. `NoProbLlama.bat` doppelklicken.
2. Oben den llama.cpp-Ordner und den Modell-Ordner wählen.
3. Ein Modell auswählen und die VRAM-Schätzung ansehen.
4. **Start** drücken, danach **Chat**, **Web-UI öffnen** oder **Speedtest**.

## Wo deine Daten liegen

Alles bleibt auf deinem Rechner, neben dem Programm:

| Datei | Inhalt |
|---|---|
| `llama_starter_config.json` | Pfade, Profile, Messwerte |
| `llama_chats.json` | Deine Chats im Klartext |
| `kontext_tests/` | Bilder der Kontext-Tests |

Diese Dateien stehen in der `.gitignore` und landen nicht im Repository.

## Dateien im Projekt

| Datei | Aufgabe |
|---|---|
| `noprobllama.py` | Hauptfenster, Serverstart, Schätzungen, Messungen |
| `llama_chat.py` | Chatfenster |
| `llama_options.py` | Optionen von `llama-server` samt Bewertung und Erklärung |
| `llama_plot.py` | Diagramme des Kontext-Tests |

## Lizenz

[MIT](LICENSE): Du darfst das Programm frei nutzen, verändern und weitergeben. Dabei muss der Copyright-Hinweis mit dem Namen des Autors erhalten bleiben.
