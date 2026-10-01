import sys
import math
import random

import re
import subprocess
import webbrowser
import datetime

import psutil

import sounddevice as sd
import pyttsx3
from faster_whisper import WhisperModel
from openai import OpenAI

from PySide6.QtCore import Qt, QTimer, QPointF, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
)


# =========================================================
# SINK PARTICLES (react to listening / thinking / speaking)
# =========================================================

class SinkCore(QWidget):

    COLORS = {
        "idle": (80, 190, 255),
        "listening": (255, 92, 138),
        "thinking": (255, 212, 92),
        "speaking": (110, 235, 255),
    }

    def __init__(self):
        super().__init__()

        self.t = 0.0
        self.state = "idle"
        self.level = 0.0
        self.target = 0.0

        self.particles = []

        for _ in range(260):
            self.particles.append({
                "angle": random.uniform(0, math.pi * 2),
                "radius": random.uniform(60, 200),
                "speed": random.uniform(0.002, 0.008)
                         * random.choice([1, -1]),
                "size": random.uniform(1, 3),
                "phase": random.uniform(0, math.pi * 2),
            })

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(16)

        self.setMinimumHeight(380)

    def set_state(self, state):
        self.state = state

    def animate(self):

        self.t += 0.016

        if self.state == "speaking":
            self.target = (
                0.5 + 0.5 * abs(math.sin(self.t * 7))
                * random.uniform(0.5, 1.0)
            )
        elif self.state == "listening":
            self.target = 0.35
        elif self.state == "thinking":
            self.target = 0.2
        else:
            self.target = 0.0

        self.level += (self.target - self.level) * 0.15

        speed_mult = 1 + self.level * 6

        for particle in self.particles:
            particle["angle"] += particle["speed"] * speed_mult

        self.update()

    def paintEvent(self, event):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor("#05070D"))

        cx = self.width() / 2
        cy = self.height() / 2

        r, g, b = self.COLORS.get(self.state, self.COLORS["idle"])

        squeeze = 1.0
        if self.state == "listening":
            squeeze = 0.75 + 0.1 * math.sin(self.t * 4)

        painter.setPen(Qt.NoPen)

        for p in self.particles:

            radius = (
                p["radius"] * squeeze
                * (1 + self.level * 0.35
                   * math.sin(p["phase"] + self.t * 8))
            )

            x = cx + math.cos(p["angle"]) * radius
            y = cy + math.sin(p["angle"]) * radius

            alpha = int(
                90 + 120 * self.level
                + 40 * math.sin(p["phase"] + self.t * 2)
            )
            alpha = max(30, min(255, alpha))

            size = p["size"] * (1 + self.level * 0.8)

            painter.setBrush(QColor(r, g, b, alpha))
            painter.drawEllipse(QPointF(x, y), size, size)


# =========================================================
# PC COMMANDS (handled BEFORE the AI, so they really work)
# =========================================================

APPS = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "file explorer": "explorer",
    "explorer": "explorer",
    "command prompt": "cmd",
    "cmd": "cmd",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "vs code": "code",
    "vscode": "code",
    "lm studio": "lms",
}

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
}


def run_command(text):
    """Returns a reply string if text was a PC command, else None."""

    t = text.lower().strip(" .!?")

    # open apps / websites
    if t.startswith("open "):
        name = t[5:].strip()

        if name in APPS:
            subprocess.Popen(
                'start "" ' + APPS[name], shell=True
            )
            return "Opening " + name + "."

        if name in SITES:
            webbrowser.open(SITES[name])
            return "Opening " + name + "."

        return "I don't know how to open " + name + " yet."

    # web search
    for prefix in ("search for ", "search ", "google "):
        if t.startswith(prefix):
            query = t[len(prefix):]
            webbrowser.open(
                "https://www.google.com/search?q="
                + query.replace(" ", "+")
            )
            return "Searching for " + query + "."

    # PC health / troubleshooting (read-only, safe)
    if "cpu" in t:
        return "CPU usage is " + str(psutil.cpu_percent(interval=1)) + " percent."

    if "ram" in t or "memory usage" in t:
        m = psutil.virtual_memory()
        return (
            "RAM is " + str(m.percent) + " percent used, "
            + str(round(m.available / 1024**3, 1)) + " GB free."
        )

    if "battery" in t:
        b = psutil.sensors_battery()
        if b is None:
            return "No battery found."
        return (
            "Battery is at " + str(int(b.percent)) + " percent"
            + (", charging." if b.power_plugged else ".")
        )

    if "disk" in t or "storage" in t:
        d = psutil.disk_usage("C:\\")
        return (
            str(round(d.free / 1024**3)) + " GB free on drive C, "
            + str(d.percent) + " percent used."
        )

    if "system status" in t or "pc status" in t or "health" in t:
        m = psutil.virtual_memory()
        return (
            "CPU " + str(psutil.cpu_percent(interval=1))
            + " percent, RAM " + str(m.percent) + " percent."
        )

    if t in ("time", "what time is it", "what is the time"):
        return "It is " + datetime.datetime.now().strftime("%I:%M %p") + "."

    return None


# =========================================================
# VOICE + AI WORKER (runs in background so the UI never freezes)
# =========================================================

stt = WhisperModel("tiny", compute_type="int8")


class Worker(QThread):

    status = Signal(str)
    heard = Signal(str)
    reply = Signal(str)

    def __init__(self, client, model_id, history, text=None):
        super().__init__()
        self.client = client
        self.model_id = model_id
        self.history = history
        self.text = text

    def speak(self, text):
        self.status.emit("SPEAKING")
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass

            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
        except Exception as error:
            print("VOICE OUTPUT ERROR:", error)

    def run(self):
        try:
            text = self.text

            # Voice input
            if text is None:
                self.status.emit("LISTENING")
                rec = sd.rec(
                    int(5 * 16000),
                    samplerate=16000,
                    channels=1,
                    dtype="float32"
                )
                sd.wait()
                segments, _ = stt.transcribe(
                    rec.flatten(), language="en"
                )
                text = " ".join(
                    s.text for s in segments
                ).strip()

                if not text:
                    self.reply.emit("I didn't catch that.")
                    return

            self.heard.emit(text)

            # PC commands first
            command_reply = run_command(text)

            if command_reply is not None:
                self.reply.emit(command_reply)
                self.speak(command_reply)
                return
            self.status.emit("THINKING")

            self.history.append(
                {"role": "user", "content": text}
            )

            result = self.client.chat.completions.create(
                model=self.model_id,
                messages=self.history
            )

            answer = result.choices[0].message.content
            # remove Qwen3 <think> blocks
            answer = re.sub(
                r"<think>.*?</think>", "",
                answer, flags=re.S
            ).strip()

            self.history.append(
                {"role": "assistant", "content": answer}
            )

            self.reply.emit(answer)

            # Voice output
            self.speak(answer)

        except Exception as error:
            self.reply.emit("SINK ERROR\n\n" + str(error))


# =========================================================
# MAIN SINK WINDOW
# =========================================================

class SinkWindow(QWidget):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("SINK AI")

        self.worker = None
        self.last_heard = ""
        self.history = [
            {
                "role": "system",
                "content": (
                    "You are SINK, a Jarvis-like voice assistant. "
                    "Reply in 1-3 short, clear sentences."
                )
            }
        ]

        self.resize(
            900,
            700
        )

        # =================================================
        # LM STUDIO CONNECTION
        # =================================================

        self.client = OpenAI(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio"
        )

        # Get model automatically
        try:

            models = self.client.models.list()

            self.model_id = models.data[0].id

        except Exception:

            self.model_id = None

        # =================================================
        # WINDOW STYLE
        # =================================================

        self.setStyleSheet("""
            QWidget {
                background-color: #05070D;
                color: #D8F6FF;
                font-family: Segoe UI;
            }

            QLineEdit {
                background-color: #0B111A;
                border: 1px solid #1C6C8C;
                border-radius: 10px;
                padding: 12px;
                color: white;
                font-size: 14px;
            }

            QLineEdit:focus {
                border: 1px solid #45D9FF;
            }

            QPushButton {
                background-color: #0B5F82;
                border: none;
                border-radius: 10px;
                padding: 12px 20px;
                color: white;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #087CA8;
            }

            QPushButton:pressed {
                background-color: #064C68;
            }
        """)

        # =================================================
        # MAIN LAYOUT
        # =================================================

        layout = QVBoxLayout()

        layout.setContentsMargins(
            30,
            20,
            30,
            25
        )

        # =================================================
        # TITLE
        # =================================================

        title = QLabel("S I N K")

        title.setAlignment(
            Qt.AlignCenter
        )

        title.setStyleSheet("""
            QLabel {
                color: #6FE7FF;
                font-size: 28px;
                font-weight: bold;
                letter-spacing: 8px;
            }
        """)

        layout.addWidget(title)

        # =================================================
        # STATUS
        # =================================================

        self.status = QLabel(
            "● SINK ONLINE"
        )

        self.status.setAlignment(
            Qt.AlignCenter
        )

        self.status.setStyleSheet("""
            QLabel {
                color: #5CFF9A;
                font-size: 13px;
            }
        """)

        layout.addWidget(
            self.status
        )

        # =================================================
        # AI CORE
        # =================================================

        self.core = SinkCore()

        layout.addWidget(
            self.core,
            1
        )

        # =================================================
        # RESPONSE BOX
        # =================================================

        self.response = QLabel(
            "Hello. I am SINK.\n"
            "Your local AI assistant is ready."
        )

        self.response.setAlignment(
            Qt.AlignCenter
        )

        self.response.setWordWrap(
            True
        )

        self.response.setStyleSheet("""
            QLabel {
                background-color: #080D15;
                border: 1px solid #12384A;
                border-radius: 12px;
                padding: 15px;
                color: #BDEFFF;
                font-size: 14px;
            }
        """)

        layout.addWidget(
            self.response
        )

        # =================================================
        # INPUT AREA
        # =================================================

        input_layout = QHBoxLayout()

        self.input = QLineEdit()

        self.input.setPlaceholderText(
            "Talk to SINK..."
        )

        self.input.returnPressed.connect(
            self.send_message
        )

        send_button = QPushButton(
            "SEND"
        )

        send_button.clicked.connect(
            self.send_message
        )

        input_layout.addWidget(
            self.input,
            1
        )

        mic_button = QPushButton(
            "🎤 SPEAK"
        )

        mic_button.clicked.connect(
            self.listen_message
        )

        input_layout.addWidget(
            mic_button
        )

        input_layout.addWidget(
            send_button
        )

        layout.addLayout(
            input_layout
        )

        self.setLayout(
            layout
        )

    # =====================================================
    # STATUS / WORKER HELPERS
    # =====================================================

    def set_status(self, text):

        colors = {
            "SINK ONLINE": "#5CFF9A",
            "LISTENING": "#FF5C8A",
            "THINKING": "#FFD45C",
            "SPEAKING": "#6FE7FF",
        }

        color = colors.get(text, "#5CFF9A")

        states = {
            "LISTENING": "listening",
            "THINKING": "thinking",
            "SPEAKING": "speaking",
        }
        self.core.set_state(states.get(text, "idle"))

        self.status.setText("● " + text)
        self.status.setStyleSheet(
            "QLabel { color: " + color +
            "; font-size: 13px; }"
        )

    def start_worker(self, text=None):

        if self.worker is not None and self.worker.isRunning():
            return

        # try to connect to LM Studio if not yet connected
        if self.model_id is None:
            try:
                self.model_id = (
                    self.client.models.list().data[0].id
                )
            except Exception:
                self.response.setText(
                    "SINK ERROR\n\n"
                    "LM Studio server is not running.\n"
                    "Load a model and click Start Server."
                )
                return

        self.worker = Worker(
            self.client,
            self.model_id,
            self.history,
            text
        )

        self.worker.status.connect(self.set_status)
        self.worker.heard.connect(self.on_heard)
        self.worker.reply.connect(self.show_reply)
        self.worker.finished.connect(
            lambda: self.set_status("SINK ONLINE")
        )

        self.worker.start()

    def on_heard(self, text):

        self.last_heard = text
        self.response.setText(
            "You: " + text +
            "\n\nSINK is thinking..."
        )

    def show_reply(self, answer):

        if self.last_heard:
            self.response.setText(
                "You: " + self.last_heard +
                "\n\nSINK: " + answer
            )
        else:
            self.response.setText(answer)

    # =====================================================
    # BUTTON ACTIONS
    # =====================================================

    def send_message(self):

        text = self.input.text().strip()

        if not text:
            return

        self.input.clear()
        self.start_worker(text)

    def listen_message(self):

        self.start_worker(None)


# =========================================================
# START SINK
# =========================================================

if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )

    window = SinkWindow()

    window.show()

    sys.exit(
        app.exec()
    )