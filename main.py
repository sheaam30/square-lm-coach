#!/usr/bin/env python3
"""
Golf Shot Analyzer
Tails a Player.log file, parses golf shot data, and streams AI feedback via Ollama.

Usage:
    python main.py
    python main.py
    python main.py --log "C:\\Users\\YourName\\AppData\\LocalLow\\Invant\\Square Golf\\Player.log"
    python main.py --model llama3.2 --ollama http://localhost:11434
"""

import argparse
import json
import math
import os
import re
import threading
import time
from datetime import datetime
from queue import Empty, Queue

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_LOG_PATH = os.path.join(
    os.path.expanduser("~"),
    "AppData", "LocalLow", "Invant", "Square Golf", "Player.log",
)
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL      = "llama3.2"
HEARTBEAT_TIMEOUT  = 10  # seconds before dot turns amber

# Club number → name mapping (adjust to match your device's numbering)
CLUB_NAMES = {
    1:  "Driver",
    2:  "3-Wood",
    3:  "5-Wood",
    4:  "3-Iron",
    5:  "4-Iron",
    6:  "5-Iron",
    7:  "6-Iron",
    8:  "7-Iron",
    9:  "8-Iron",
    10: "9-Iron",
    11: "PW",
    12: "GW",
    13: "SW",
    14: "LW",
    15: "Putter",
}

DEFAULT_PROMPT = """\
You are an expert golf instructor analyzing real-time launch monitor data.
Give concise, actionable feedback with 2-3 specific tips and feel cues.
Focus on the most impactful changes the golfer can make.

Club: {club_name}  |  Handed: {handed}

SHOT DATA:
  Ball Speed   : {ball_speed_mph} mph
  Launch Angle : {launch_angle}°
  Direction    : {side_angle}°
  Spin Rate    : {backspin} rpm
  Spin Axis    : {carry}°
  Back Spin    : {total_dist} rpm
  Side Spin    : {side_dist} rpm
  Carry        : {carry_yards} yds
  Side         : {side_dist_yards} yds

CLUB DATA:
  Club Path      : {club_speed}°
  Face to Target : {attack_angle}°
  Attack Angle   : {club_path}°   (negative = descending blow)
  Dynamic Loft   : {face_angle}°

Provide your feedback below:\
"""

# ── Regex patterns ────────────────────────────────────────────────────────────
_SHOT_RE = re.compile(
    r"Recv Shot Data : "
    r"([\d.]+)\((\w+)\), "      # ball_speed, valid
    r"([\d.-]+), "               # launch_angle  (no validity flag)
    r"([\d.-]+), "               # side_angle    (no validity flag)
    r"(\d+)\((\w+)\), "         # backspin, valid
    r"([\d.-]+)\((\w+)\), "     # carry, valid
    r"(\d+)\((\w+)\), "         # total_dist, valid
    r"([\d.-]+)\((\w+)\)"       # side_dist, valid
)

_CLUB_RE = re.compile(
    r"Recv Club Data : "
    r"([\d.-]+)\((\w+)\), "     # club_speed, valid
    r"([\d.-]+)\((\w+)\), "     # attack_angle, valid
    r"([\d.-]+)\((\w+)\), "     # club_path, valid
    r"([\d.-]+)\((\w+)\)"       # face_angle, valid
)

_HEARTBEAT_RE = re.compile(r"^71000000")

_REMAIN_RE = re.compile(r"PlayerController:AutoClub:: remainDistance\(([\d.]+)\)")

_STATUS_RE = re.compile(
    r"RX : GetStatus \w+, club_sel (\d+), club_num (\d+), handed (\d+)"
)


# ── Parsers ───────────────────────────────────────────────────────────────────
def parse_shot(line: str):
    m = _SHOT_RE.search(line)
    if not m:
        return None
    g = m.groups()
    return {
        "ball_speed":        float(g[0]),  "ball_speed_valid":  g[1] == "True",
        "launch_angle":      float(g[2]),
        "side_angle":        float(g[3]),
        "backspin":          int(g[4]),    "backspin_valid":    g[5] == "True",
        "carry":             float(g[6]),  "carry_valid":       g[7] == "True",
        "total_dist":        int(g[8]),    "total_dist_valid":  g[9] == "True",
        "side_dist":         float(g[10]), "side_dist_valid":   g[11] == "True",
    }


def parse_club(line: str):
    m = _CLUB_RE.search(line)
    if not m:
        return None
    g = m.groups()
    return {
        "club_speed":        float(g[0]), "club_speed_valid":   g[1] == "True",
        "attack_angle":      float(g[2]), "attack_angle_valid": g[3] == "True",
        "club_path":         float(g[4]), "club_path_valid":    g[5] == "True",
        "face_angle":        float(g[6]), "face_angle_valid":   g[7] == "True",
    }


def parse_status(line: str):
    m = _STATUS_RE.search(line)
    if not m:
        return None
    return {
        "club_sel": int(m.group(1)),
        "club_num": int(m.group(2)),
        "handed":   "Left" if m.group(3) == "1" else "Right",
    }


# ── Carry estimation ─────────────────────────────────────────────────────────
def _estimate_carry(ball_speed_ms: float, launch_deg: float) -> float:
    """
    Estimate carry distance in yards using simple projectile motion.

    Empirically matches the Square Golf simulator's carry display — verified
    against a user-reported data point (21.65 m/s / 24.79° → 39.9 yds).
    No air resistance or spin lift modelled.

    Args:
        ball_speed_ms: Ball speed in m/s (field 1 from device).
        launch_deg:    Launch angle in degrees (field 2 from device).

    Returns:
        Carry in yards, or 0.0 for non-positive inputs.
    """
    if ball_speed_ms <= 0 or launch_deg <= 0:
        return 0.0
    carry_m = ball_speed_ms ** 2 * math.sin(math.radians(2 * launch_deg)) / 9.81
    return round(carry_m / 0.9144, 1)


def _estimate_side(carry_yards: float, side_angle_deg: float) -> float:
    """
    Estimate lateral offset in yards at carry distance.

    Approximates the ball's offline distance assuming a straight flight at
    the given launch direction. Positive = right, negative = left.

    Args:
        carry_yards:    Carry distance in yards.
        side_angle_deg: Horizontal launch direction in degrees (field 3 from device).

    Returns:
        Side distance in yards (rounded to 1 dp).
    """
    return round(carry_yards * math.sin(math.radians(side_angle_deg)), 1)


# ── Log tailer ────────────────────────────────────────────────────────────────
class LogTailer:
    """Reads new lines appended to a file and puts parsed events on a queue."""

    def __init__(self, path: str, event_queue: Queue):
        self.path = path
        self.queue = event_queue
        self._stop = threading.Event()
        self._thread = None  # type: threading.Thread

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="log-tailer")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        self.queue.put(("status", f"Opening: {self.path}"))
        while not self._stop.is_set():
            try:
                with open(self.path, "rb") as f:
                    f.seek(0, 2)  # jump to end — ignore history
                    self.queue.put(("status", "Tailing log…"))
                    buf = b""
                    while not self._stop.is_set():
                        chunk = f.read(4096)
                        if not chunk:
                            time.sleep(0.05)
                            continue
                        # Strip null bytes (the log has a large null-padded header)
                        chunk = chunk.replace(b"\x00", b"")
                        buf += chunk
                        while b"\n" in buf:
                            line_bytes, buf = buf.split(b"\n", 1)
                            line = line_bytes.decode("utf-8", errors="replace").strip()
                            if line:
                                self._process(line)
            except FileNotFoundError:
                self.queue.put(("status", f"File not found — retrying: {self.path}"))
                time.sleep(3)
            except Exception as exc:
                self.queue.put(("status", f"Tailer error: {exc} — retrying…"))
                time.sleep(3)

    def _process(self, line: str):
        if _HEARTBEAT_RE.search(line):
            self.queue.put(("heartbeat", None))
            return
        shot = parse_shot(line)
        if shot:
            self.queue.put(("shot_data", shot))
            return
        club = parse_club(line)
        if club:
            self.queue.put(("club_data", club))
            return
        status = parse_status(line)
        if status:
            self.queue.put(("status_data", status))
            return
        m = _REMAIN_RE.search(line)
        if m:
            self.queue.put(("remain_dist", float(m.group(1))))


# ── Ollama client ─────────────────────────────────────────────────────────────
def query_ollama(url: str, model: str, prompt: str,
                 on_token, on_done):
    """Streams tokens from Ollama in a background thread."""
    def _run():
        try:
            resp = requests.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": True},
                stream=True,
                timeout=120,
            )
            if resp.status_code == 404:
                on_done(f"Model '{model}' not found. Pull it first:\n  ollama pull {model}")
                return
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if raw:
                    data = json.loads(raw)
                    token = data.get("response", "")
                    if token:
                        on_token(token)
                    if data.get("done"):
                        break
            on_done(None)
        except requests.exceptions.ConnectionError:
            on_done(f"Cannot connect to Ollama at {url}\nMake sure Ollama is running:  ollama serve")
        except requests.exceptions.Timeout:
            on_done(f"Ollama request timed out (120 s). Try a smaller/faster model.")
        except Exception as exc:
            on_done(str(exc))

    threading.Thread(target=_run, daemon=True, name="ollama").start()


# ── Markdown cleaner ─────────────────────────────────────────────────────────
def _clean_markdown(text: str) -> str:
    """Strip common markdown symbols so LLM output reads cleanly in plain text."""
    # Bold/italic: **text**, *text*, ***text***
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    # ATX headers: ## Heading → Heading
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # Inline code: `code`
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Collapse 3+ blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── GUI ───────────────────────────────────────────────────────────────────────
BG       = "#1e1e2e"
BG2      = "#181825"
FG       = "#cdd6f4"
FG_DIM   = "#a6adc8"
ACCENT   = "#89b4fa"
GREEN    = "#a6e3a1"
AMBER    = "#f9e2af"
RED      = "#f38ba8"
MONO     = ("Consolas", 10)
SANS     = ("Segoe UI", 10)
SANS_B   = ("Segoe UI", 10, "bold")
HEADER   = ("Segoe UI", 11, "bold")


def _style(style: ttk.Style):
    style.theme_use("clam")
    for widget in ("TFrame", "TLabelframe", "TLabelframe.Label"):
        style.configure(widget, background=BG)
    style.configure("TLabel",       background=BG,  foreground=FG,      font=SANS)
    style.configure("Dim.TLabel",   background=BG,  foreground=FG_DIM,  font=SANS)
    style.configure("Header.TLabel",background=BG,  foreground=ACCENT,  font=HEADER)
    style.configure("Value.TLabel", background=BG,  foreground=GREEN,   font=MONO)
    style.configure("Warn.TLabel",  background=BG,  foreground=AMBER,   font=MONO)
    style.configure("TButton",      font=SANS)
    style.configure("TEntry",       font=SANS, fieldbackground=BG2,
                    foreground=FG, insertcolor=FG)
    style.configure("TSeparator",   background="#313244")


class App(tk.Tk):
    def __init__(self, log_path: str, model: str, ollama_url: str):
        super().__init__()
        self.title("Golf Shot Analyzer")
        self.geometry("960x720")
        self.minsize(760, 560)
        self.configure(bg=BG)

        self._queue           = Queue()
        self._tailer = None  # type: LogTailer
        self._pending_shot    = None   # shot data waiting for matching club data
        self._last_status     = {}     # club_sel / club_num / handed from device
        self._last_heartbeat  = 0.0
        self._llm_busy        = False
        self._llm_raw         = ""     # accumulated LLM response for post-clean render
        self._remain_dist     = None   # last known remaining distance to hole (yds)
        self._last_shot_data  = None   # merged shot dict from most recent complete shot

        _style(ttk.Style(self))
        self._build_ui(log_path, model, ollama_url)
        self._start_tailer(log_path)
        self._poll()

    # ── Build UI ──────────────────────────────────────────────────────────

    def _build_ui(self, log_path: str, model: str, ollama_url: str):
        # ── Settings bar ──────────────────────────────────────────────────
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=10, pady=(10, 0))

        ttk.Label(bar, text="Log File:").pack(side=tk.LEFT)
        self._path_var = tk.StringVar(value=log_path)
        ttk.Entry(bar, textvariable=self._path_var, width=36).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="Browse…", command=self._browse).pack(side=tk.LEFT)
        ttk.Button(bar, text="Restart", command=self._restart_tailer).pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(bar, text="Model:").pack(side=tk.LEFT)
        self._model_var = tk.StringVar(value=model)
        ttk.Entry(bar, textvariable=self._model_var, width=16).pack(side=tk.LEFT, padx=4)

        ttk.Label(bar, text="Ollama:").pack(side=tk.LEFT, padx=(8, 0))
        self._ollama_var = tk.StringVar(value=ollama_url)
        ttk.Entry(bar, textvariable=self._ollama_var, width=24).pack(side=tk.LEFT, padx=4)

        # ── Status row ────────────────────────────────────────────────────
        srow = ttk.Frame(self)
        srow.pack(fill=tk.X, padx=10, pady=(4, 0))
        self._dot = ttk.Label(srow, text="●", foreground=RED, font=("Segoe UI", 12))
        self._dot.pack(side=tk.LEFT)
        self._status_var = tk.StringVar(value="Waiting…")
        ttk.Label(srow, textvariable=self._status_var, style="Dim.TLabel").pack(side=tk.LEFT, padx=6)

        ttk.Separator(self).pack(fill=tk.X, padx=10, pady=6)

        # ── Main two-column area ──────────────────────────────────────────
        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=0)

        # Left column — shot data
        left = ttk.Frame(main, width=260)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        shot_header = ttk.Frame(left)
        shot_header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(shot_header, text="LAST SHOT", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Button(shot_header, text="Export", command=self._export_shot).pack(side=tk.RIGHT)

        self._fields: dict[str, ttk.Label] = {}
        rows = [
            ("Club",         "club_name"),
            ("Handed",       "handed"),
            None,
            ("Ball Speed",    "ball_speed"),
            ("Launch Angle",  "launch_angle"),
            ("Direction",     "side_angle"),
            ("Spin Rate",     "backspin"),
            ("Spin Axis",     "carry"),
            ("Back Spin",     "total_dist"),
            ("Side Spin",     "side_dist"),
            ("Carry",         "carry_yards"),
            ("Side",          "side_dist_yards"),
            None,
            ("Club Path",     "club_speed"),
            ("Face to Target","attack_angle"),
            ("Attack Angle",  "club_path"),
            ("Dynamic Loft",  "face_angle"),
            None,
            ("To Hole",       "remain_dist"),
        ]
        for item in rows:
            if item is None:
                ttk.Separator(left).pack(fill=tk.X, pady=4)
                continue
            label, key = item
            row = ttk.Frame(left)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"{label}:", width=14, anchor=tk.W).pack(side=tk.LEFT)
            lbl = ttk.Label(row, text="—", style="Value.TLabel")
            lbl.pack(side=tk.LEFT)
            self._fields[key] = lbl

        # Right column — feedback
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(right, text="AI FEEDBACK", style="Header.TLabel").pack(anchor=tk.W, pady=(0, 4))
        self._feedback = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, state=tk.DISABLED,
            bg=BG2, fg=FG, font=("Segoe UI", 10),
            relief=tk.FLAT, insertbackground=FG,
        )
        self._feedback.pack(fill=tk.BOTH, expand=True)

        # ── Prompt editor ─────────────────────────────────────────────────
        ttk.Separator(self).pack(fill=tk.X, padx=10, pady=6)
        ph = ttk.Frame(self)
        ph.pack(fill=tk.X, padx=10)
        ttk.Label(ph, text="PROMPT TEMPLATE", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            ph,
            text="  — edit freely; uses Python .format() with shot/club field names",
            style="Dim.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(ph, text="Reset to default", command=self._reset_prompt).pack(side=tk.RIGHT)

        self._prompt_editor = scrolledtext.ScrolledText(
            self, wrap=tk.WORD, height=8,
            bg=BG2, fg=FG_DIM, font=MONO,
            relief=tk.FLAT, insertbackground=FG,
        )
        self._prompt_editor.pack(fill=tk.X, padx=10, pady=(4, 10))
        self._prompt_editor.insert(tk.END, DEFAULT_PROMPT)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select Player.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if path:
            self._path_var.set(path)
            self._restart_tailer()

    def _reset_prompt(self):
        self._prompt_editor.delete("1.0", tk.END)
        self._prompt_editor.insert(tk.END, DEFAULT_PROMPT)

    def _export_shot(self):
        """Save the last shot's raw data and AI feedback to a JSON file."""
        if not self._last_shot_data:
            messagebox.showinfo("Export", "No shot data yet — hit a shot first.")
            return

        path = filedialog.asksaveasfilename(
            title="Export shot data",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        if not path:
            return

        feedback_text = self._feedback.get("1.0", tk.END).strip()
        export = {
            "timestamp": datetime.now().isoformat(),
            "shot": {k: v for k, v in self._last_shot_data.items()},
            "ai_feedback": feedback_text,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2)
        messagebox.showinfo("Export", f"Shot data saved to:\n{path}")

    # ── Tailer management ─────────────────────────────────────────────────

    def _start_tailer(self, path: str):
        if self._tailer:
            self._tailer.stop()
        self._tailer = LogTailer(path, self._queue)
        self._tailer.start()

    def _restart_tailer(self):
        self._start_tailer(self._path_var.get())

    # ── Event loop ────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                event, data = self._queue.get_nowait()
                self._handle(event, data)
        except Empty:
            pass

        # Heartbeat indicator
        elapsed = time.time() - self._last_heartbeat
        if self._last_heartbeat > 0:
            if elapsed < HEARTBEAT_TIMEOUT:
                self._dot.configure(foreground=GREEN)
            else:
                self._dot.configure(foreground=AMBER)

        self.after(100, self._poll)

    def _handle(self, event: str, data):
        if event == "status":
            self._status_var.set(data)
        elif event == "heartbeat":
            self._last_heartbeat = time.time()
        elif event == "status_data":
            self._last_status = data
        elif event == "shot_data":
            self._pending_shot = data
        elif event == "club_data":
            if self._pending_shot:
                self._on_complete_shot(self._pending_shot, data)
                self._pending_shot = None
        elif event == "remain_dist":
            self._remain_dist = data
            if "remain_dist" in self._fields:
                self._fields["remain_dist"].configure(text=f"{data:.1f} yds")
        elif event == "llm_token":
            self._llm_raw += data
            self._feedback.config(state=tk.NORMAL)
            self._feedback.insert(tk.END, data)
            self._feedback.see(tk.END)
            self._feedback.config(state=tk.DISABLED)
        elif event == "llm_done":
            self._llm_busy = False
            if data:  # error string
                self._feedback.config(state=tk.NORMAL)
                self._feedback.insert(tk.END, f"\n\n[Error: {data}]")
                self._feedback.config(state=tk.DISABLED)
            else:
                # Re-render the completed response with markdown stripped
                cleaned = _clean_markdown(self._llm_raw)
                self._feedback.config(state=tk.NORMAL)
                # Preserve the timestamp/header line, replace the rest
                content = self._feedback.get("1.0", tk.END)
                header = content.split("\n\n", 1)[0] + "\n\n"
                self._feedback.delete("1.0", tk.END)
                self._feedback.insert(tk.END, header + cleaned)
                self._feedback.see(tk.END)
                self._feedback.config(state=tk.DISABLED)

    # ── Shot handling ─────────────────────────────────────────────────────

    def _on_complete_shot(self, shot: dict, club: dict):
        merged = {**shot, **club}

        # Enrich with device status
        club_num = self._last_status.get("club_num", 0)
        merged["club_name"]      = CLUB_NAMES.get(club_num, f"Club #{club_num}")
        merged["handed"]         = self._last_status.get("handed", "Right")

        # Convert ball speed m/s → mph to match simulator display
        merged["ball_speed_mph"] = round(merged["ball_speed"] * 2.237, 1)

        # Estimate carry and side distance from ball speed + launch angle
        merged["carry_yards"]     = _estimate_carry(merged["ball_speed"], merged["launch_angle"])
        merged["side_dist_yards"] = _estimate_side(merged["carry_yards"], merged["side_angle"])

        # Validity suffix helper
        def v(key: str, unit: str = "") -> str:
            val   = merged[key]
            valid = merged.get(f"{key}_valid", True)
            text  = f"{val}{unit}"
            return text if valid else f"{text} (?)"

        # Update shot panel
        self._fields["club_name"].configure(text=merged["club_name"])
        self._fields["handed"].configure(text=merged["handed"])
        self._fields["ball_speed"].configure(text=v("ball_speed_mph", " mph"))
        self._fields["launch_angle"].configure(text=f"{merged['launch_angle']}°")
        self._fields["side_angle"].configure(text=f"{merged['side_angle']}°")
        self._fields["backspin"].configure(text=v("backspin",       " rpm"))
        self._fields["carry"].configure(text=v("carry", "°"))
        self._fields["total_dist"].configure(text=v("total_dist",            " rpm"))
        self._fields["side_dist"].configure(text=v("side_dist",              " rpm"))
        self._fields["carry_yards"].configure(text=f'{merged["carry_yards"]} yds')
        self._fields["side_dist_yards"].configure(text=f'{merged["side_dist_yards"]} yds')
        self._fields["club_speed"].configure(text=v("club_speed",            "°"))
        self._fields["attack_angle"].configure(text=v("attack_angle","°"))
        self._fields["club_path"].configure(text=v("club_path",     "°"))
        self._fields["face_angle"].configure(text=v("face_angle",   "°"))

        # Store for export
        self._last_shot_data = merged

        # Query LLM (skip if already processing a shot)
        if not self._llm_busy:
            self._query_llm(merged)

    def _query_llm(self, data: dict):
        self._llm_busy = True
        self._llm_raw  = ""
        ts = datetime.now().strftime("%H:%M:%S")

        self._feedback.config(state=tk.NORMAL)
        self._feedback.delete("1.0", tk.END)
        self._feedback.insert(tk.END, f"[{ts}] Analyzing {data['club_name']} shot…\n\n")
        self._feedback.config(state=tk.DISABLED)

        template = self._prompt_editor.get("1.0", tk.END).strip()
        try:
            prompt = template.format(**data)
        except KeyError as e:
            self._feedback.config(state=tk.NORMAL)
            self._feedback.insert(tk.END, f"Prompt template error — unknown key: {e}\n")
            self._feedback.config(state=tk.DISABLED)
            self._llm_busy = False
            return

        query_ollama(
            url=self._ollama_var.get(),
            model=self._model_var.get(),
            prompt=prompt,
            on_token=lambda tok: self._queue.put(("llm_token", tok)),
            on_done=lambda err: self._queue.put(("llm_done", err)),
        )


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Golf Shot Analyzer")
    ap.add_argument("--log",    default=DEFAULT_LOG_PATH,   help="Path to Player.log")
    ap.add_argument("--model",  default=DEFAULT_MODEL,      help="Ollama model name")
    ap.add_argument("--ollama", default=DEFAULT_OLLAMA_URL, help="Ollama server URL")
    args = ap.parse_args()

    App(log_path=args.log, model=args.model, ollama_url=args.ollama).mainloop()


if __name__ == "__main__":
    main()
