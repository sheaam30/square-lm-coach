# square-lm-coach

A Windows desktop app that tails a golf launch monitor log file in real time, parses shot and club data, and streams AI coaching feedback via a local [Ollama](https://ollama.com/) model.

![dark UI with shot data panel on the left and streaming AI feedback on the right]

---

## Running the pre-built .exe (Windows)

1. Download the latest `square-lm-coach-vX.Y.Z-windows.exe` from the [Releases page](https://github.com/sheaam30/square-lm-coach/releases)
2. Double-click to run — no Python or installer required
3. **Windows SmartScreen warning?** Click **More info → Run anyway** (the app is unsigned)
4. Install [Ollama](https://ollama.com/) and pull a model:
   ```
   ollama pull llama3.2
   ```
5. Set the **Log File** field to your launch monitor's `Player.log` (default: `C:\Users\Public\Player.log`)
6. Hit shots — parsed data appears on the left, AI feedback streams in on the right

> **Ollama is not bundled.** It must be installed and running separately.

---

## What the UI shows

| Panel | Content |
|---|---|
| **Status dot** | Green = heartbeat active, Amber = signal stale, Red = file not found |
| **Last Shot** | Ball speed, launch angle, side angle, backspin, carry, total dist, side dist, club speed, attack angle, club path, face angle |
| **AI Feedback** | Streaming coaching tips and feel cues from your local LLM |
| **Prompt Template** | Editable prompt — tweak tone, focus, or field labels without restarting |

---

## CLI options

```
square-lm-coach.exe --log "C:\path\to\Player.log"
square-lm-coach.exe --log "C:\path\to\Player.log" --model llama3.1:8b
square-lm-coach.exe --ollama http://192.168.1.10:11434
```

| Flag | Default | Description |
|---|---|---|
| `--log` | `C:\Users\Public\Player.log` | Path to the launch monitor log file |
| `--model` | `llama3.2` | Ollama model name (must be pulled first) |
| `--ollama` | `http://localhost:11434` | Ollama server URL |

All three flags are also editable in the UI at runtime.

---

## Choosing an Ollama model

| Model | RAM needed | Quality |
|---|---|---|
| `llama3.2:1b` | ~2 GB | Fast, basic |
| `llama3.2` (3B) | ~4 GB | Good balance |
| `llama3.1:8b` | ~8 GB | Best feedback |

```
ollama pull llama3.2
```

---

## Building from source

**Prerequisites:** Python 3.9+, Windows (for the `.exe` output)

```
git clone https://github.com/sheaam30/square-lm-coach.git
cd square-lm-coach
pip install pyinstaller requests pytest
```

Run the tests first:
```
pytest tests/
```

Build the executable:
```
python build.py --clean --version v1.0.0
# Output: dist/square-lm-coach-v1.0.0-windows.exe
```

Or use PyInstaller directly:
```
pyinstaller golf_shot_analyzer.spec
# Output: dist/square-lm-coach.exe
```

---

## Creating a release

Releases are built automatically by GitHub Actions when a version tag is pushed:

```
git tag v1.0.0
git push origin v1.0.0
```

The workflow will:
1. Run the full test suite on `windows-latest`
2. Build the `.exe` with PyInstaller
3. Publish a GitHub Release with the `.exe` attached

Every push to `main` also builds a dev artifact (downloadable from the Actions tab, kept for 30 days).

---

## Running tests

```
pytest tests/ -v
```

The test suite covers all log parsers, heartbeat detection, the tailer dispatch logic, null-byte stripping, and the club name mapping — 93 tests total.

---

## Log format

The app reads `Player.log` files produced by SQG-compatible launch monitors (BLE). It looks for three line types:

| Line | Example |
|---|---|
| **Heartbeat** | `710000000000378ed8 - 4,751.630` |
| **Shot data** | `Recv Shot Data : 21.65(True), 24.79, 0.13, 4538(True), 5.57(True), 4517(True), 441(True)` |
| **Club data** | `Recv Club Data : 4.37(True), -0.92(True), -6.55(True), 33.84(True)` |

Shot + Club lines always appear together; the app combines them into a single event before sending to the LLM.
