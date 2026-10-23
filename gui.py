import sys
import socket
SINK_PORT = 48765
_SINK_SERVER = None


def _claim_single_instance():

    global _SINK_SERVER

    msg = b"dashboard" if "--dashboard" in sys.argv else b"toggle"

    try:
        with socket.create_connection(
            ("127.0.0.1", SINK_PORT),
            timeout=0.3,
        ) as s:
            s.sendall(msg)
        return False
    except OSError:
        pass

    try:
        # No SO_REUSEADDR on purpose: on Windows it would allow
        # two SINK copies to share the same port.
        srv = socket.socket()
        srv.bind(("127.0.0.1", SINK_PORT))
        srv.listen(5)
        _SINK_SERVER = srv
    except OSError:
        pass

    return True


if __name__ == "__main__" and not _claim_single_instance():
    sys.exit(0)

import math
import random
import re
import difflib
import subprocess
import webbrowser
import datetime
import time
import os
import json
import threading
import io
import base64

import numpy as np
import psutil
import pyautogui
import sounddevice as sd
import pyttsx3

from faster_whisper import WhisperModel
from openai import OpenAI

from PySide6.QtCore import (
    Qt,
    QObject,
    QTimer,
    QPointF,
    QThread,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QIcon,
    QPixmap,
    QColor,
    QPainter,
    QPen,
    QFont,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QMenu,
    QSystemTrayIcon,
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QCheckBox,
)


# =========================================================
# SAFETY
# =========================================================

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


# =========================================================
# WAKE WORD
# =========================================================

WAKE_PATTERN = (
    r"^(?:hey |hi |ok |okay )?"
    r"(?:sink|sync|think|zinc|zink|sing|sank)\b[,.! ]*(.*)$"
)


INITIAL_PROMPT = (
    "SINK. Short desktop voice command. "
    "Examples: open Chrome, open YouTube, open yt, "
    "scroll down, scroll up, click, press click, tap, "
    "double click, right click, hit enter, go to YouTube, "
    "move mouse right 200, press enter, type hello, "
    "click send, what's on my screen, solve what I copied, "
    "what can I say, learn study mode, run study mode, "
    "friend mode, assistant mode, call me Sir, call me Mam."
)


# =========================================================
# SAFETY + SPEECH CLEANING
# =========================================================

BLOCKED_TERMS = (
    "system32",
    "program files",
    "windowsapps",
    "registry",
    "regedit",
    "password",
    "credentials",
    "credential",
    "token",
    "ssh",
    "delete file",
    "delete folder",
    "format drive",
    "shutdown",
    "restart",
)

BLOCKED_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(re.escape(x) for x in BLOCKED_TERMS)
    + r")\b",
    re.I,
)


# "I love you" / "so cute" -> blush expression
LOVE_PATTERN = (
    r"\b(?:i love you|i really love you|love you|"
    r"i love u|love u|luv you|luv u)\b"
)

CUTE_PATTERN = (
    r"\b(?:so cute|too cute|very cute|"
    r"(?:you|u|ur|you're|youre|you are|you r) cute|"
    r"you look cute|cutie)\b"
)


def is_blocked_request(text):
    return bool(BLOCKED_PATTERN.search(text))


def clean_for_speech(text):
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.S | re.I,
    )

    text = re.sub(
        r"```.*?```",
        " code omitted ",
        text,
        flags=re.S,
    )

    text = re.sub(
        r"https?://\S+",
        " link ",
        text,
    )

    text = re.sub(
        r"[^\w\s.,!?%:'\"()/-]",
        " ",
        text,
        flags=re.UNICODE,
    )

    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()

    return text


# =========================================================
# PERSONA  (Mam / Sir  +  Assistant / Friend mode)
# =========================================================

PERSONA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sink_settings.json",
)

PERSONA_DEFAULT = {"address": "Mam", "mode": "assistant"}

# No command for this many minutes -> SINK goes to sleep
# (stops the microphone listener and hides the chibi).
# The shortcut key wakes it again.
AUTO_SLEEP_MIN = 10


def persona_load():
    try:
        with open(PERSONA_FILE, "r", encoding="utf-8") as f:
            return {**PERSONA_DEFAULT, **json.load(f)}
    except (OSError, ValueError):
        return dict(PERSONA_DEFAULT)


def persona_set(key, value):
    data = persona_load()
    data[key] = value
    try:
        with open(PERSONA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as error:
        print("PERSONA SAVE ERROR:", error)


def persona_text():
    p = persona_load()

    if p["mode"] == "friend":
        return (
            "SINK persona: FRIEND MODE. Talk like a warm, caring close "
            "friend and mentor. This overrides the earlier brevity rule: "
            "when the user asks for advice, a lesson, or wants to talk "
            "about feelings, answer in up to 6 short spoken sentences. "
            "Be honest and kind, not just flattering. Call the user "
            + p["address"]
            + " now and then, in a friendly way. Plain spoken text only, "
            "no markdown or lists."
        )

    return (
        "SINK persona: ASSISTANT MODE. Be polite and professional. "
        "Address the user as "
        + p["address"]
        + "."
    )


def persona_command(t):
    """Return a short spoken reply if t is a persona command, else None."""

    if re.search(r"\b(?:friend) mode\b", t):
        persona_set("mode", "friend")
        return "Friend mode on. I'm all ears."

    if re.search(r"\b(?:assistant) mode\b", t):
        persona_set("mode", "assistant")
        return "Assistant mode on."

    if re.search(r"\bcall me (?:ma'?am|mam|madam)\b", t):
        persona_set("address", "Mam")
        return "Sure, Mam."

    if re.search(r"\bcall me sir\b", t):
        persona_set("address", "Sir")
        return "Sure, Sir."

    return None


# =========================================================
# CHIBI ROBOT
# =========================================================

class ChibiRobot(QWidget):

    clicked = Signal()

    def __init__(self):
        super().__init__()

        self.emotion = "idle"
        self.status_text = ""

        self.t = 0.0
        self.blink = 0
        self.next_blink = random.randint(100, 220)

        # -------------------------------------------------
        # ANIMATION STATES
        # -------------------------------------------------

        self.dance = False
        self.wake_animation = False
        self.wake_start = 0.0

        # NEW:
        # Prevent THINKING/SPEAKING from overwriting love.
        self.love_mode = False
        self.love_start = 0.0

        # Blush (shy face) has its own protected state too.
        self.blush_mode = False
        self.blush_start = 0.0

        # Physical dance movement so the animation is unmistakable.
        self.dance_base_pos = None

        # "SOLVING..." word above the head while a long task runs
        self.solving = False

        # Bigger canvas prevents clipping
        self.setFixedSize(180, 225)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )

        self.setAttribute(
            Qt.WA_TranslucentBackground
        )

        self.setAttribute(
            Qt.WA_ShowWithoutActivating
        )

        self.move_mode = False
        self.drag_offset = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(40)

    # -----------------------------------------------------

    def set_emotion(self, emotion):

        allowed = {
            "idle",
            "happy",
            "sad",
            "thinking",
            "listening",
            "speaking",
            "blush",
            "love",
        }

        if emotion not in allowed:
            return

        # DO NOT overwrite special animations
        if self.love_mode:
            return

        if self.blush_mode:
            return

        if self.wake_animation:
            return

        self.emotion = emotion
        self.update()

    # -----------------------------------------------------

    def set_solving(self, flag):

        self.solving = bool(flag)

        self.update()

    # -----------------------------------------------------

    def set_status_text(self, text):

        # DO NOT overwrite special animation text
        if self.love_mode:
            return

        if self.blush_mode:
            return

        if self.wake_animation:
            return

        self.status_text = text
        self.update()

    # -----------------------------------------------------
    # WAKE / DANCE
    # -----------------------------------------------------

    def wake_up(self):

        # Love has priority
        if self.love_mode:
            return

        if self.blush_mode:
            return

        self.wake_animation = True
        self.dance = True
        self.wake_start = self.t
        self.dance_base_pos = self.pos()

        self.emotion = "happy"
        self.status_text = "SINK ♥"

        self.show()
        self.raise_()
        self.update()

    # -----------------------------------------------------

    def stop_wake_animation(self):

        if self.love_mode:
            return

        self.dance = False
        self.wake_animation = False
        self.dance_base_pos = None

        self.emotion = "idle"
        self.status_text = ""

        self.update()

    # -----------------------------------------------------
    # LOVE MODE
    # -----------------------------------------------------

    def show_love(self):

        self.wake_animation = False
        self.dance = False

        self.blush_mode = False

        self.love_mode = True
        self.love_start = self.t
        self.dance_base_pos = None

        self.emotion = "love"
        self.status_text = "♥"

        self.show()
        self.raise_()
        self.update()

    # -----------------------------------------------------

    def stop_love(self):

        self.love_mode = False

        self.emotion = "idle"
        self.status_text = ""

        self.update()

    # -----------------------------------------------------
    # BLUSH MODE
    # ("I love you" / "SINK you are so cute")
    # -----------------------------------------------------

    def show_blush(self):

        self.wake_animation = False
        self.dance = False
        self.dance_base_pos = None

        # An explicit compliment beats the idle heart eyes.
        self.love_mode = False

        self.blush_mode = True
        self.blush_start = self.t

        self.emotion = "blush"
        self.status_text = "HEHE ♥"

        self.show()
        self.raise_()
        self.update()

    # -----------------------------------------------------

    def stop_blush(self):

        if not self.blush_mode:
            return

        self.blush_mode = False

        if not self.love_mode:

            self.emotion = "idle"
            self.status_text = ""

        self.update()

    # -----------------------------------------------------

    def animate(self):

        self.t += 0.04

        # -------------------------------------------------
        # BLINK
        # -------------------------------------------------

        if self.blink > 0:

            self.blink -= 1

        else:

            self.next_blink -= 1

            if self.next_blink <= 0:

                self.blink = 5

                self.next_blink = random.randint(
                    100,
                    240,
                )

        # -------------------------------------------------
        # WAKE / DANCE TIMER
        # -------------------------------------------------

        if self.wake_animation:

            # Move the actual Chibi window as well as its drawing.
            # This makes the dance visible even if repaint timing is subtle.
            if self.dance_base_pos is not None:
                dx = int(math.sin(self.t * 12) * 22)
                dy = int(abs(math.sin(self.t * 12)) * -14)
                self.move(
                    self.dance_base_pos.x() + dx,
                    self.dance_base_pos.y() + dy,
                )

            if self.t - self.wake_start > 5.0:

                self.stop_wake_animation()

        # -------------------------------------------------
        # LOVE TIMER
        # -------------------------------------------------

        if self.love_mode:

            if self.t - self.love_start > 4.0:

                self.stop_love()

        if self.blush_mode:

            if self.t - self.blush_start > 4.0:

                self.stop_blush()

        self.update()

    # -----------------------------------------------------

    def mousePressEvent(self, event):

        if (
            event.button() == Qt.LeftButton
            and self.move_mode
        ):

            self.drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )

        self.clicked.emit()
        event.accept()

    # -----------------------------------------------------

    def mouseMoveEvent(self, event):

        if (
            self.move_mode
            and self.drag_offset is not None
        ):

            self.move(
                event.globalPosition().toPoint()
                - self.drag_offset
            )

        event.accept()

    # -----------------------------------------------------

    def mouseReleaseEvent(self, event):

        self.drag_offset = None
        event.accept()

    # -----------------------------------------------------

    def mouseDoubleClickEvent(self, event):

        self.move_mode = not self.move_mode

        if self.move_mode:

            self.status_text = "MOVE MODE"

        else:

            self.status_text = ""

        self.update()
        event.accept()

    # -----------------------------------------------------

    def paintEvent(self, event):

        p = QPainter(self)

        p.setRenderHint(
            QPainter.Antialiasing
        )

        cx = self.width() / 2

        # -------------------------------------------------
        # NORMAL FLOATING
        # -------------------------------------------------

        bob = math.sin(
            self.t * 2.5
        ) * 2

        # -------------------------------------------------
        # DANCE
        # -------------------------------------------------

        dance_x = 0
        dance_y = 0

        if self.dance:

            dance_x = (
                math.sin(self.t * 12)
                * 18
            )

            dance_y = (
                abs(
                    math.sin(self.t * 12)
                )
                * -12
            )

        # -------------------------------------------------
        # STATUS TEXT
        # -------------------------------------------------

        status = self.status_text

        if self.solving:

            k = int(self.t * 3) % 4

            status = (
                "SOLVING"
                + "." * k
                + "\u00a0" * (3 - k)
            )

        if status:

            font = QFont(
                "Segoe UI",
                10,
            )

            font.setBold(True)

            p.setFont(font)

            text_rect = self.rect()

            text_rect.setTop(2)
            text_rect.setBottom(27)

            for alpha in (
                35,
                70,
                150,
            ):

                p.setPen(
                    QColor(
                        255,
                        215,
                        90,
                        alpha,
                    )
                )

                p.drawText(
                    text_rect,
                    Qt.AlignCenter,
                    status,
                )

            p.setPen(
                QColor("#FFE7A0")
            )

            p.drawText(
                text_rect,
                Qt.AlignCenter,
                status,
            )

        # -------------------------------------------------
        # ROBOT POSITION
        # -------------------------------------------------

        robot_x = cx + dance_x

        body_y = (
            145
            + bob
            + dance_y
        )

        head_y = (
            94
            + bob
            + dance_y
        )

        antenna_y1 = (
            52
            + bob
            + dance_y
        )

        antenna_y2 = (
            27
            + bob
            + dance_y
        )

        # -------------------------------------------------
        # GLOW
        # -------------------------------------------------

        p.setPen(Qt.NoPen)

        if self.love_mode or self.blush_mode:

            p.setBrush(
                QColor(
                    255,
                    80,
                    140,
                    35,
                )
            )

        else:

            p.setBrush(
                QColor(
                    255,
                    210,
                    80,
                    22,
                )
            )

        p.drawEllipse(
            QPointF(
                robot_x,
                145 + bob + dance_y,
            ),
            74,
            74,
        )

        # -------------------------------------------------
        # BODY
        # -------------------------------------------------

        p.setBrush(
            QColor("#17130A")
        )

        p.setPen(
            QPen(
                QColor("#D9A92E"),
                3,
            )
        )

        p.drawRoundedRect(
            int(robot_x - 43),
            int(body_y),
            86,
            58,
            25,
            25,
        )

        # -------------------------------------------------
        # HEAD
        # -------------------------------------------------

        p.setBrush(
            QColor("#FFF0C2")
        )

        p.setPen(
            QPen(
                QColor("#D9A92E"),
                3,
            )
        )

        p.drawEllipse(
            QPointF(
                robot_x,
                head_y,
            ),
            52,
            47,
        )

        # -------------------------------------------------
        # ANTENNA
        # -------------------------------------------------

        p.setPen(
            QPen(
                QColor("#D9A92E"),
                3,
            )
        )

        p.drawLine(
            QPointF(
                robot_x,
                antenna_y1,
            ),
            QPointF(
                robot_x,
                antenna_y2,
            ),
        )

        p.setBrush(
            QColor("#FFD65A")
        )

        p.setPen(Qt.NoPen)

        p.drawEllipse(
            QPointF(
                robot_x,
                antenna_y2 - 2,
            ),
            6,
            6,
        )

        # -------------------------------------------------
        # EYES
        # -------------------------------------------------

        eye_y = (
            91
            + bob
            + dance_y
        )

        left = QPointF(
            robot_x - 20,
            eye_y,
        )

        right = QPointF(
            robot_x + 20,
            eye_y,
        )

        # -------------------------------------------------
        # LOVE EYES
        # -------------------------------------------------

        if self.emotion == "love":

            p.setPen(Qt.NoPen)

            p.setBrush(
                QColor("#FF5C8A")
            )

            # Left heart
            left_heart = QPolygonF([
                QPointF(
                    robot_x - 20,
                    eye_y + 8,
                ),
                QPointF(
                    robot_x - 32,
                    eye_y - 3,
                ),
                QPointF(
                    robot_x - 31,
                    eye_y - 10,
                ),
                QPointF(
                    robot_x - 24,
                    eye_y - 13,
                ),
                QPointF(
                    robot_x - 20,
                    eye_y - 8,
                ),
                QPointF(
                    robot_x - 16,
                    eye_y - 13,
                ),
                QPointF(
                    robot_x - 9,
                    eye_y - 10,
                ),
                QPointF(
                    robot_x - 8,
                    eye_y - 3,
                ),
            ])

            # Right heart
            right_heart = QPolygonF([
                QPointF(
                    robot_x + 20,
                    eye_y + 8,
                ),
                QPointF(
                    robot_x + 8,
                    eye_y - 3,
                ),
                QPointF(
                    robot_x + 9,
                    eye_y - 10,
                ),
                QPointF(
                    robot_x + 16,
                    eye_y - 13,
                ),
                QPointF(
                    robot_x + 20,
                    eye_y - 8,
                ),
                QPointF(
                    robot_x + 24,
                    eye_y - 13,
                ),
                QPointF(
                    robot_x + 31,
                    eye_y - 10,
                ),
                QPointF(
                    robot_x + 32,
                    eye_y - 3,
                ),
            ])

            p.drawPolygon(
                left_heart
            )

            p.drawPolygon(
                right_heart
            )

        # -------------------------------------------------
        # NORMAL / OTHER EYES
        # -------------------------------------------------

        elif self.blink:

            p.setPen(
                QPen(
                    QColor("#3A2A20"),
                    3,
                )
            )

            p.drawLine(
                QPointF(
                    robot_x - 26,
                    eye_y,
                ),
                QPointF(
                    robot_x - 14,
                    eye_y,
                ),
            )

            p.drawLine(
                QPointF(
                    robot_x + 14,
                    eye_y,
                ),
                QPointF(
                    robot_x + 26,
                    eye_y,
                ),
            )

        elif self.emotion == "sad":

            p.setBrush(
                QColor("#3A2A20")
            )

            p.setPen(Qt.NoPen)

            p.drawEllipse(
                left,
                5,
                7,
            )

            p.drawEllipse(
                right,
                5,
                7,
            )

            p.setPen(
                QPen(
                    QColor("#3A2A20"),
                    3,
                )
            )

            p.drawLine(
                QPointF(
                    robot_x - 27,
                    eye_y - 9,
                ),
                QPointF(
                    robot_x - 15,
                    eye_y - 5,
                ),
            )

            p.drawLine(
                QPointF(
                    robot_x + 15,
                    eye_y - 5,
                ),
                QPointF(
                    robot_x + 27,
                    eye_y - 9,
                ),
            )

        elif self.blush_mode:

            # Shy, happy closed eyes  ^   ^
            p.setBrush(Qt.NoBrush)

            p.setPen(
                QPen(
                    QColor("#3A2A20"),
                    3,
                )
            )

            p.drawArc(
                int(robot_x - 27),
                int(eye_y - 5),
                14,
                10,
                15 * 16,
                150 * 16,
            )

            p.drawArc(
                int(robot_x + 13),
                int(eye_y - 5),
                14,
                10,
                15 * 16,
                150 * 16,
            )

        elif self.emotion == "thinking":

            p.setBrush(
                QColor("#3A2A20")
            )

            p.setPen(Qt.NoPen)

            p.drawEllipse(
                left,
                4,
                6,
            )

            p.drawEllipse(
                right,
                4,
                6,
            )

            p.setPen(
                QPen(
                    QColor("#3A2A20"),
                    3,
                )
            )

            p.drawLine(
                QPointF(
                    robot_x - 27,
                    eye_y - 10,
                ),
                QPointF(
                    robot_x - 14,
                    eye_y - 12,
                ),
            )

        else:

            p.setBrush(
                QColor("#3A2A20")
            )

            p.setPen(Qt.NoPen)

            p.drawEllipse(
                left,
                5,
                7,
            )

            p.drawEllipse(
                right,
                5,
                7,
            )

        # -------------------------------------------------
        # BLUSH
        # -------------------------------------------------

        if self.emotion in (
            "blush",
            "love",
        ):

            p.setPen(Qt.NoPen)

            p.setBrush(
                QColor(
                    255,
                    100,
                    120,
                    150,
                )
            )

            p.drawEllipse(
                QPointF(
                    robot_x - 35,
                    eye_y + 14,
                ),
                9,
                5,
            )

            p.drawEllipse(
                QPointF(
                    robot_x + 35,
                    eye_y + 14,
                ),
                9,
                5,
            )

        # -------------------------------------------------
        # BIG BLUSH (only for "I love you" / "so cute")
        # -------------------------------------------------

        if self.blush_mode:

            pulse = 0.5 + 0.5 * math.sin(self.t * 6)

            p.setPen(Qt.NoPen)

            p.setBrush(
                QColor(
                    255,
                    70,
                    110,
                    int(170 + 70 * pulse),
                )
            )

            for sx in (-35, 35):

                p.drawEllipse(
                    QPointF(
                        robot_x + sx,
                        eye_y + 14,
                    ),
                    13,
                    7,
                )

            p.setPen(
                QPen(
                    QColor(255, 255, 255, 210),
                    1.5,
                )
            )

            for sx in (-35, 35):

                for k in (-6, 0, 6):

                    p.drawLine(
                        QPointF(
                            robot_x + sx + k - 2,
                            eye_y + 17,
                        ),
                        QPointF(
                            robot_x + sx + k + 2,
                            eye_y + 10,
                        ),
                    )

        # -------------------------------------------------
        # MOUTH
        # -------------------------------------------------

        p.setPen(
            QPen(
                QColor("#3A2A20"),
                3,
            )
        )

        if self.emotion in (
            "happy",
            "speaking",
            "blush",
            "love",
        ):

            p.drawArc(
                int(robot_x - 18),
                int(
                    98
                    + bob
                    + dance_y
                ),
                36,
                24,
                200 * 16,
                140 * 16,
            )

        elif self.emotion == "sad":

            p.drawArc(
                int(robot_x - 18),
                int(
                    108
                    + bob
                    + dance_y
                ),
                36,
                20,
                20 * 16,
                140 * 16,
            )

        else:

            p.drawLine(
                QPointF(
                    robot_x - 9,
                    111
                    + bob
                    + dance_y,
                ),
                QPointF(
                    robot_x + 9,
                    111
                    + bob
                    + dance_y,
                ),
            )

        # -------------------------------------------------
        # FLOATING HEART
        # -------------------------------------------------

        if self.emotion == "love":

            heart_y = (
                42
                + math.sin(
                    self.t * 3
                ) * 3
            )

            p.setPen(Qt.NoPen)

            p.setBrush(
                QColor("#FF5C8A")
            )

            path = QPolygonF([
                QPointF(
                    robot_x,
                    heart_y + 18,
                ),
                QPointF(
                    robot_x - 18,
                    heart_y + 2,
                ),
                QPointF(
                    robot_x - 18,
                    heart_y - 7,
                ),
                QPointF(
                    robot_x - 10,
                    heart_y - 13,
                ),
                QPointF(
                    robot_x,
                    heart_y - 5,
                ),
                QPointF(
                    robot_x + 10,
                    heart_y - 13,
                ),
                QPointF(
                    robot_x + 18,
                    heart_y - 7,
                ),
                QPointF(
                    robot_x + 18,
                    heart_y + 2,
                ),
            ])

            p.drawPolygon(path)

        # -------------------------------------------------
        # CHEST LIGHT
        # -------------------------------------------------

        light = {
            "idle": "#FFD65A",
            "happy": "#7CFFB2",
            "sad": "#6EA8FF",
            "thinking": "#FFD45C",
            "listening": "#FFB45C",
            "speaking": "#FFE36A",
            "blush": "#FF7894",
            "love": "#FF5C8A",
        }.get(
            self.emotion,
            "#FFD65A",
        )

        p.setBrush(
            QColor(light)
        )

        p.setPen(Qt.NoPen)

        p.drawEllipse(
            QPointF(
                robot_x,
                173
                + bob
                + dance_y,
            ),
            7,
            7,
        )

        p.end()


# =========================================================
# SINK PARTICLES
# =========================================================

class SinkCore(QWidget):

    COLORS = {
        "idle": (
            255,
            200,
            60,
        ),
        "listening": (
            255,
            140,
            70,
        ),
        "thinking": (
            255,
            235,
            150,
        ),
        "speaking": (
            255,
            215,
            0,
        ),
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
                "angle": random.uniform(
                    0,
                    math.pi * 2,
                ),
                "radius": random.uniform(
                    60,
                    200,
                ),
                "speed": (
                    random.uniform(
                        0.002,
                        0.008,
                    )
                    * random.choice(
                        [1, -1]
                    )
                ),
                "size": random.uniform(
                    1,
                    3,
                ),
                "phase": random.uniform(
                    0,
                    math.pi * 2,
                ),
            })

        self.timer = QTimer(self)

        self.timer.timeout.connect(
            self.animate
        )

        self.timer.start(16)

        self.setMinimumHeight(380)

    # -----------------------------------------------------

    def set_state(self, state):
        self.state = state

    # -----------------------------------------------------

    def animate(self):

        self.t += 0.016

        if self.state == "speaking":

            self.target = (
                0.5
                + 0.5
                * abs(
                    math.sin(
                        self.t * 7
                    )
                )
                * random.uniform(
                    0.5,
                    1.0,
                )
            )

        elif self.state == "listening":

            self.target = 0.35

        elif self.state == "thinking":

            self.target = 0.2

        else:

            self.target = 0.0

        self.level += (
            self.target
            - self.level
        ) * 0.15

        speed_mult = (
            1
            + self.level * 6
        )

        for particle in self.particles:

            particle["angle"] += (
                particle["speed"]
                * speed_mult
            )

        self.update()

    # -----------------------------------------------------

    def paintEvent(self, event):

        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.Antialiasing
        )

        painter.fillRect(
            self.rect(),
            QColor("#070604"),
        )

        cx = self.width() / 2
        cy = self.height() / 2

        r, g, b = self.COLORS.get(
            self.state,
            self.COLORS["idle"],
        )

        squeeze = 1.0

        if self.state == "listening":

            squeeze = (
                0.75
                + 0.1
                * math.sin(
                    self.t * 4
                )
            )

        painter.setPen(Qt.NoPen)

        for particle in self.particles:

            radius = (
                particle["radius"]
                * squeeze
                * (
                    1
                    + self.level
                    * 0.35
                    * math.sin(
                        particle["phase"]
                        + self.t * 8
                    )
                )
            )

            x = (
                cx
                + math.cos(
                    particle["angle"]
                )
                * radius
            )

            y = (
                cy
                + math.sin(
                    particle["angle"]
                )
                * radius
            )

            twinkle = (
                0.5
                + 0.5
                * math.sin(
                    particle["phase"] * 3
                    + self.t * 4
                )
            )

            alpha = int(
                (
                    70
                    + 130 * self.level
                )
                * (
                    0.35
                    + 0.65 * twinkle
                )
            )

            alpha = max(
                20,
                min(
                    255,
                    alpha,
                ),
            )

            size = (
                particle["size"]
                * (
                    1
                    + self.level * 0.8
                )
            )

            if twinkle > 0.9:

                painter.setBrush(
                    QColor(
                        r,
                        g,
                        b,
                        60,
                    )
                )

                painter.drawEllipse(
                    QPointF(x, y),
                    size * 3.2,
                    size * 3.2,
                )

                painter.setBrush(
                    QColor(
                        255,
                        250,
                        225,
                        255,
                    )
                )

                painter.drawEllipse(
                    QPointF(x, y),
                    size * 1.1,
                    size * 1.1,
                )

            else:

                painter.setBrush(
                    QColor(
                        r,
                        g,
                        b,
                        alpha,
                    )
                )

                painter.drawEllipse(
                    QPointF(x, y),
                    size,
                    size,
                )


# =========================================================
# APPS / WEBSITES
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

    "camera": "microsoft.windows.camera:",

    "vs code": "code",
    "vscode": "code",

    "lm studio": "lms",
}


SITES = {

    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",

    "google": "https://www.google.com",

    "gmail": "https://mail.google.com",

    "github": "https://github.com",

    "chatgpt": "https://chatgpt.com",
}


# =========================================================
# KEYS
# =========================================================

KEYS = {

    "enter": "enter",
    "return": "enter",

    "escape": "esc",
    "esc": "esc",

    "tab": "tab",

    "space": "space",

    "backspace": "backspace",

    "delete": "delete",

    "up": "up",
    "up arrow": "up",

    "down": "down",
    "down arrow": "down",

    "left": "left",
    "left arrow": "left",

    "right": "right",
    "right arrow": "right",

    "home": "home",
    "end": "end",

    "page up": "pageup",
    "pageup": "pageup",

    "page down": "pagedown",
    "pagedown": "pagedown",
}


SHORTCUTS = {

    "copy": (
        "ctrl",
        "c",
    ),

    "paste": (
        "ctrl",
        "v",
    ),

    "cut": (
        "ctrl",
        "x",
    ),

    "select all": (
        "ctrl",
        "a",
    ),

    "undo": (
        "ctrl",
        "z",
    ),

    "redo": (
        "ctrl",
        "y",
    ),

    "save": (
        "ctrl",
        "s",
    ),

    "new tab": (
        "ctrl",
        "t",
    ),

    "close tab": (
        "ctrl",
        "w",
    ),

    "close window": (
        "alt",
        "f4",
    ),

    "switch window": (
        "alt",
        "tab",
    ),

    "go back": (
        "alt",
        "left",
    ),

    "go forward": (
        "alt",
        "right",
    ),

    "refresh": (
        "f5",
    ),

    "zoom in": (
        "ctrl",
        "=",
    ),

    "zoom out": (
        "ctrl",
        "-",
    ),

    "maximize window": (
        "win",
        "up",
    ),

    "minimize window": (
        "win",
        "down",
    ),

    "show desktop": (
        "win",
        "d",
    ),
}


# =========================================================
# COMMAND SPLITTING
# =========================================================

STEP_SPLIT = re.compile(
    r"\s+(?:and then|and|then)\s+"
    r"(?=(?:"
    r"scroll|"
    r"press|"
    r"click|"
    r"double click|"
    r"right click|"
    r"left click|"
    r"type|"
    r"write|"
    r"open|"
    r"search|"
    r"google|"
    r"copy|"
    r"paste|"
    r"cut|"
    r"select all|"
    r"close|"
    r"switch|"
    r"go back|"
    r"go forward|"
    r"new tab|"
    r"enter|"
    r"undo|"
    r"redo|"
    r"refresh|"
    r"zoom|"
    r"maximize|"
    r"minimize|"
    r"minimise|"
    r"maximise|"
    r"tap|"
    r"hit|"
    r"reload|"
    r"next tab|"
    r"previous tab|"
    r"full screen|"
    r"move"
    r")\b)",
    re.I,
)


CONTROL_WORDS = re.compile(
    r"^(?:"
    r"type|"
    r"write|"
    r"press|"
    r"click|"
    r"double click|"
    r"right click|"
    r"left click|"
    r"scroll|"
    r"move (?:the )?mouse|"
    r"copy|"
    r"paste|"
    r"cut|"
    r"select all|"
    r"undo|"
    r"redo|"
    r"save|"
    r"new tab|"
    r"close tab|"
    r"close window|"
    r"switch window|"
    r"go back|"
    r"go forward|"
    r"refresh|"
    r"zoom|"
    r"maximize|"
    r"minimize|"
    r"show desktop|"
    r"next tab|"
    r"previous tab|"
    r"reopen tab|"
    r"new window|"
    r"full screen|"
    r"screenshot|"
    r"find on page|"
    r"enter|"
    r"escape|"
    r"tab|"
    r"space|"
    r"backspace"
    r")\b",
    re.I,
)


# =========================================================
# VOICE SYNONYMS
# Different ways of saying the same thing -> ONE command.
# "press click", "tap", "left click", "click it" -> "click"
# =========================================================

SHORTCUTS.update({
    "full screen": ("f11",),
    "next tab": ("ctrl", "tab"),
    "previous tab": ("ctrl", "shift", "tab"),
    "reopen tab": ("ctrl", "shift", "t"),
    "new window": ("ctrl", "n"),
    "find on page": ("ctrl", "f"),
    "screenshot": ("win", "shift", "s"),
})


FILLER_LEAD = re.compile(
    r"^(?:(?:please|pls|plz|kindly|hey|ok|okay|so|just|now|then|"
    r"can you|could you|would you|will you|can u|could u|"
    r"go ahead and|i want you to|i need you to|you can|try to|"
    r"let's|lets)[\s,]+)+",
    re.I,
)

FILLER_TAIL = re.compile(
    r"(?:[\s,]+(?:please|pls|plz|for me|right now|kar do|kardo|"
    r"kar de|karo|kro|kr do|krdo|na|yaar|bro|thanks|thank you))+$",
    re.I,
)


def strip_fillers(text, tail=True):
    text = FILLER_LEAD.sub("", text.strip())

    if tail:
        text = FILLER_TAIL.sub("", text)

    return text.strip()


# ---------------------------------------------------------
# numbers: "two hundred" -> 200
# ---------------------------------------------------------

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}

_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def words_to_int(text):
    total = 0
    current = 0
    seen = False

    for w in text.replace("-", " ").split():

        if w == "and":
            continue

        if w.isdigit():
            current += int(w)
        elif w in _ONES:
            current += _ONES[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            return None

        seen = True

    return total + current if seen else None


# ---------------------------------------------------------
# Hinglish + things Whisper often hears wrong
# ---------------------------------------------------------

HINGLISH = [
    (r"^(.+?) (?:kholo|khol do|khol de|kholna|khol)$", r"open \1"),
    (r"^(.+?) band$", r"close \1"),
    (r"\b(?:neeche|niche|nichey)\b", "down"),
    (r"\b(?:upar|oopar|upr)\b", "up"),
    (r"\b(?:daayein|dayein|dahine|daye)\b", "right"),
    (r"\b(?:baayein|bayein|baye)\b", "left"),
]

HEARING_FIXES = [
    (r"\b(?:press|hit|push) tap\b", "press tab"),
    (r"\b(?:clique|clic|clik|klick|clicks|clicked|clicking)\b", "click"),
    (r"(?<!press )(?<!hit )(?<!push )\b(?:tap|taps|tapped|tapping)\b",
     "click"),
    (r"\bdoubleclick\b", "double click"),
    (r"\brightclick\b", "right click"),
    (r"\bleftclick\b", "left click"),
    (r"\b(?:scrawl|scrawls|skroll|scrolls|scrolled|scrolling|scroller)\b",
     "scroll"),
    (r"\bminimi[sz]e\b", "minimize"),
    (r"\bmaximi[sz]e\b", "maximize"),
    (r"\bscreen shot\b", "screenshot"),
    (r"\bfull ?screen\b", "full screen"),
    (r"\bspace ?bar\b", "space"),
    (r"\bback ?space\b", "backspace"),
    (r"\bpage ?(up|down)\b", r"page \1"),
    (r"\b(?:upwards|upward)\b", "up"),
    (r"\b(?:downwards|downward)\b", "down"),
]


# ---------------------------------------------------------
# click / double click / right click
# ---------------------------------------------------------

_CLICK_NOISE = {
    "press", "hit", "push", "do", "make", "give", "perform", "use",
    "the", "a", "an", "one", "mouse", "button", "on", "it", "this",
    "that", "here", "there", "once", "with", "my", "left", "single",
    "key",
}

_CLICK_MODS = {"double", "twice", "right", "times", "two", "2x"}


def _click_kind(t):
    words = t.split()

    if not words or len(words) > 8:
        return None

    if "click" not in words:

        if (
            words[0] in ("press", "hit", "push")
            and ("mouse" in words or "button" in words)
            and set(words) <= _CLICK_NOISE
        ):
            return "click"

        return None

    others = [
        w for w in words
        if w != "click"
        and w not in _CLICK_NOISE
        and w not in _CLICK_MODS
    ]

    if others:
        return None

    if (
        "double" in words
        or "twice" in words
        or "2x" in words
        or ("two" in words and "times" in words)
    ):
        return "double click"

    if "right" in words:
        return "right click"

    return "click"


# ---------------------------------------------------------
# keys: "hit enter", "press the enter key", "space bar"
# ---------------------------------------------------------

KEY_ALIASES = {
    "del": "delete",
    "arrow up": "up arrow",
    "arrow down": "down arrow",
    "arrow left": "left arrow",
    "arrow right": "right arrow",
    "submit": "enter",
    "cancel": "escape",
}


def _key_command(t):
    m = re.match(
        r"^(?:press|hit|push|click)\s+(?:the\s+)?(.+?)"
        r"(?:\s+(?:key|button))?$",
        t,
    )

    if m:
        name = m.group(1).strip()
        name = KEY_ALIASES.get(name, name)

        return "press " + name if name in KEYS else None

    m = re.match(r"^(.+?)\s+(?:key|button)$", t)

    if m:
        name = m.group(1).strip()
        name = KEY_ALIASES.get(name, name)

        return "press " + name if name in KEYS else None

    if t in KEY_ALIASES:
        return "press " + KEY_ALIASES[t]

    return None


# ---------------------------------------------------------
# scroll: "go down", "go to the top", "scroll the page down"
# ---------------------------------------------------------

def _scroll_command(t):
    m = re.match(
        r"^(?:go|move|come|jump|take me)\s+(?:to\s+)?(?:the\s+)?"
        r"(top|bottom|up|down)"
        r"(?:\s+(?:of\s+)?(?:the\s+)?(?:page|screen))?$",
        t,
    )

    if m:
        return "scroll " + m.group(1)

    m = re.match(
        r"^scroll\s+(?:the\s+)?(?:page|screen|window|it|this|that)\s+(.+)$",
        t,
    )

    if m:
        return "scroll " + m.group(1)

    return None


# ---------------------------------------------------------
# mouse: "move the pointer right two hundred"
# ---------------------------------------------------------

def _mouse_command(t):
    t = re.sub(r"^move (?:the )?(?:cursor|pointer|arrow)\b", "move mouse", t)
    t = re.sub(
        r"^(?:mouse|cursor|pointer)\s+(?=(?:left|right|up|down)\b)",
        "move mouse ",
        t,
    )
    t = re.sub(r"^move (?:the )?mouse\b", "move mouse", t)

    if not t.startswith("move mouse"):
        return None

    rest = t[len("move mouse"):].strip()

    m = re.match(r"^(?:to\s+)?(\d+)\s*(?:by|and|comma|x|,)\s*(\d+)$", rest)

    if m:
        return f"move mouse {m.group(1)} {m.group(2)}"

    skip = {
        "to", "the", "towards", "by", "for", "pixels", "pixel",
        "pix", "px", "steps", "step",
    }

    tokens = [w for w in rest.split() if w not in skip]

    direction = None

    for w in tokens:
        if w in ("left", "right", "up", "down"):
            direction = w
            tokens.remove(w)
            break

    if direction is None:
        return None

    tokens = [w for w in tokens if w != "a"]

    if not tokens:
        return f"move mouse {direction}"

    if any(w in ("bit", "little", "slightly") for w in tokens):
        return f"move mouse {direction} 80"

    if any(w in ("lot", "far", "more") for w in tokens):
        return f"move mouse {direction} 500"

    n = words_to_int(" ".join(tokens))

    if n:
        return f"move mouse {direction} {n}"

    return f"move mouse {direction}"


# ---------------------------------------------------------
# windows / tabs / editing / opening / searching / time
# ---------------------------------------------------------

_DET = r"(?:(?:this|the|current|that|my)\s+)?"

SYNONYMS = [
    # tabs
    (r"^(?:close|shut)\s+" + _DET + r"(?:tab|page)$", "close tab"),
    (r"^(?:open\s+)?(?:a\s+|another\s+)?new\s+tab$", "new tab"),
    (r"^(?:open\s+)?(?:a\s+|another\s+)?new\s+window$", "new window"),
    (r"^(?:(?:go|switch|move)\s+to\s+)?(?:the\s+)?next\s+tab$", "next tab"),
    (r"^switch\s+tabs?$", "next tab"),
    (r"^(?:(?:go|switch|move)\s+to\s+)?(?:the\s+)?"
     r"(?:previous|prev|last)\s+tab$", "previous tab"),
    (r"^(?:reopen|restore|open)\s+(?:the\s+)?(?:last\s+|closed\s+|previous\s+)?"
     r"(?:closed\s+)?tab$", "reopen tab"),

    # windows
    (r"^(?:(?:close|shut|quit|exit|kill)\s+" + _DET
     + r"(?:window|app|application|program)|(?:close|shut|quit)"
     r"(?:\s+(?:this|it|that))?)$", "close window"),
    (r"^(?:switch|change|toggle|alt tab)"
     r"(?:\s+(?:to\s+)?(?:the\s+)?(?:other\s+|next\s+|another\s+|"
     r"previous\s+|last\s+)?(?:window|windows|app|apps|application|"
     r"program))?$", "switch window"),
    (r"^(?:(?:show|go to|go)\s+(?:the\s+)?desktop|"
     r"minimize all(?:\s+windows)?)$", "show desktop"),
    (r"^minimize(?:\s+(?:this|it|that|the|current))*"
     r"(?:\s+(?:window|app|application|program))?$", "minimize window"),
    (r"^(?:maximize(?:\s+(?:this|it|that|the|current))*"
     r"(?:\s+(?:window|app|application|program))?|"
     r"make\s+(?:it|this|the window)\s+(?:full|fullscreen))$",
     "maximize window"),
    (r"^(?:(?:go|make it|enter|exit|toggle)\s+)?full screen"
     r"(?:\s+mode)?$", "full screen"),

    # browser
    (r"^(?:refresh|reload)(?:\s+(?:this|the|current))?"
     r"(?:\s+(?:page|tab|website|site|browser))?$", "refresh"),
    (r"^(?:go\s+)?back(?:\s+(?:to\s+)?(?:the\s+)?(?:previous|last)\s+page)?$",
     "go back"),
    (r"^(?:go\s+)?forward(?:\s+(?:a\s+)?page)?$", "go forward"),
    (r"^(?:go to|open)\s+(?:the\s+)?previous page$", "go back"),
    (r"^find\s+on\s+(?:this\s+)?page$|^search\s+(?:on|in)\s+(?:this\s+)?page$",
     "find on page"),

    # editing
    (r"^copy(?:\s+(?:this|that|it|text|selected(?:\s+text)?|selection|"
     r"the selected text))?$", "copy"),
    (r"^paste(?:\s+(?:this|that|it|here|text|clipboard|the text|"
     r"what i copied))?$", "paste"),
    (r"^cut(?:\s+(?:this|that|it|text|selected(?:\s+text)?|selection))?$",
     "cut"),
    (r"^(?:select\s+(?:all|everything)(?:\s+text)?|highlight all|"
     r"select the whole (?:page|text|thing))$", "select all"),
    (r"^undo(?:\s+(?:this|that|it|last|last action|the last thing))?$",
     "undo"),
    (r"^redo(?:\s+(?:this|that|it|last|last action))?$", "redo"),
    (r"^save(?:\s+(?:this|that|it|file|the file|document|the document|"
     r"my work|changes))?$", "save"),
    (r"^(?:zoom in|increase zoom|make (?:it|this|the page) "
     r"(?:bigger|larger))$", "zoom in"),
    (r"^(?:zoom out|decrease zoom|reduce zoom|make (?:it|this|the page) "
     r"(?:smaller))$", "zoom out"),
    (r"^(?:take\s+(?:a\s+)?)?screenshot$|^capture (?:the )?screen$",
     "screenshot"),

    # open / search
    (r"^(?:go to|take me to|navigate to|visit|run|bring up|switch to)\s+"
     r"(?!sleep\b|top\b|bottom\b|the top|the bottom|desktop|the desktop|"
     r"previous|next|end\b)(.+)$", r"open \1"),
    (r"^(?:look up|lookup|search up|search about|search google for|"
     r"google search|search on google for|search on google|"
     r"search in google for|google for|search the web for|find me|find)"
     r"\s+(.+)$", r"search \1"),

    # time
    (r"^(?:what(?:\s+is|\s+s|s)?|tell me|say|show|give me)?\s*"
     r"(?:the\s+)?(?:current\s+|exact\s+)?time"
     r"(?:\s+is\s+it)?(?:\s+now|\s+right now)?$", "what time is it"),
]


def normalize_command(t):
    """Turn many ways of saying a command into one canonical command."""

    t = (t or "").lower().strip()
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = strip_fillers(t)

    for pattern, repl in HINGLISH:
        t = re.sub(pattern, repl, t)

    for pattern, repl in HEARING_FIXES:
        t = re.sub(pattern, repl, t)

    t = re.sub(r"\s+", " ", t).strip()
    t = strip_fillers(t)

    for fn in (_click_kind, _key_command, _scroll_command, _mouse_command):

        result = fn(t)

        if result:
            return result

    for pattern, repl in SYNONYMS:

        m = re.match(pattern, t)

        if m:
            return m.expand(repl)

    return t


def _prepare_step(step):
    cleaned = re.sub(r"[^\w\s-]", " ", step.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return normalize_command(cleaned)


def needs_focus(text):

    for step in STEP_SPLIT.split(
        text.strip(
            " .!,?"
        ).lower()
    ):

        if CONTROL_WORDS.match(
            _prepare_step(step)
        ):

            return True

    return False


# =========================================================
# RUN ONE COMMAND
# =========================================================

# =========================================================
# YOUTUBE: search + click the first video
# "search lofi music on youtube and click first video"
# =========================================================

YT_FIRST = (
    r"(?:\s+(?:and|then|and then)\s+(?:click|play|open|select)"
    r"(?:\s+on)?(?:\s+the)?\s+(?:first|top|1st)\s+"
    r"(?:video|result|one))?"
)


def click_first_video():
    # Best effort: finds the first normal video link on a YouTube
    # page using Windows UI Automation (needs pywinauto).
    try:
        import win32gui
        from pywinauto import Desktop
    except Exception:
        return "Click needs pywinauto installed."

    try:
        hwnd = win32gui.GetForegroundWindow()
        win = Desktop(backend="uia").window(handle=hwnd)

        # Only act on a YouTube window, never on some other site.
        if "youtube" not in win.window_text().lower():
            return "I don't see YouTube open."

        links = win.descendants(control_type="Hyperlink")

        print("FIRST VIDEO: hyperlinks seen:", len(links))

        for el in links:
            try:
                label = el.window_text().strip().lower()

                if not label or not el.is_visible():
                    continue

                # A video link's name has views/watching or a length
                # like "3 hours, 10 minutes".
                looks_like_video = re.search(
                    r"\b(?:views?|watching)\b"
                    r"|\b\d+\s*(?:hours?|minutes?|seconds?)\b",
                    label,
                )

                if (
                    looks_like_video
                    and not re.search(r"sponsored|\bad\b|shorts?", label)
                ):
                    print("FIRST VIDEO: clicking:", label[:90])
                    el.click_input()
                    return "Playing the first video."

            except Exception:
                pass

        return "I couldn't find a video on this page."

    except Exception as error:
        print("CLICK FIRST VIDEO ERROR:", error)
        return "I couldn't click the first video."


def debug_links():
    # Prints what Windows can see in the front window (for fixing).
    try:
        import win32gui
        from pywinauto import Desktop

        hwnd = win32gui.GetForegroundWindow()
        win = Desktop(backend="uia").window(handle=hwnd)

        print("DEBUG window title:", win.window_text())

        links = win.descendants(control_type="Hyperlink")

        print("DEBUG hyperlinks:", len(links))

        shown = 0

        for el in links:
            label = el.window_text().strip()

            if label:
                print("DEBUG link:", label[:90])
                shown += 1

            if shown >= 25:
                break

        return (
            f"I can see {len(links)} links. "
            "I printed the first ones in the console."
        )

    except Exception as error:
        print("DEBUG ERROR:", error)
        return "Debug failed. Check the console."


def click_first_video_wait():
    # The page may still be loading, so try a few times.
    reply = "I couldn't find a video on this page."

    for _ in range(4):
        reply = click_first_video()

        if reply.startswith("Playing"):
            return reply

        time.sleep(2)

    return reply


def first_video_url(query):
    # Finds the first video for a search WITHOUT clicking anything.
    # Needs:  pip install yt-dlp   (if missing, SINK falls back to
    # clicking the page).
    try:
        import yt_dlp
    except Exception:
        return None

    try:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                "ytsearch1:" + query,
                download=False,
            )

        entries = info.get("entries") or []

        if entries and entries[0].get("id"):
            return "https://www.youtube.com/watch?v=" + entries[0]["id"]

    except Exception as error:
        print("FIRST VIDEO URL ERROR:", error)

    return None


def youtube_step(t):
    import urllib.parse

    m = re.match(
        r"^(?:search|find|look up|play|watch)\s+(?:for\s+)?(.+?)"
        r"\s+(?:on|in)\s+youtube" + YT_FIRST + "$",
        t,
    ) or re.match(
        r"^(?:search|look up)\s+youtube\s+(?:for\s+)?(.+?)" + YT_FIRST + "$",
        t,
    )

    if m:
        query = m.group(1).strip()
        click_first = bool(
            re.search(r"\b(?:first|top|1st)\b", t[m.end(1):])
        )

        if click_first:
            video_url = first_video_url(query)

            if video_url:
                webbrowser.open(video_url)
                return "Playing the first video for " + query + "."

        webbrowser.open(
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(query)
        )

        if not click_first:
            return "Opening YouTube search for " + query + "."

        time.sleep(4)

        return click_first_video_wait()

    if t in ("debug links", "debug link"):
        return debug_links()

    if re.match(
        r"^(?:click|play|open|select)(?:\s+on)?(?:\s+the)?\s+"
        r"(?:first|top|1st)\s+(?:video|result|one)$",
        t,
    ):
        return click_first_video_wait()

    return None


def run_step(text):

    orig = (
        text
        .strip()
        .rstrip(
            " .!?,"
        )
    )

    # "please type hello" -> "type hello"
    orig = strip_fillers(
        orig,
        tail=False,
    )

    t = re.sub(
        r"[^\w\s-]",
        " ",
        orig.lower(),
    )

    t = re.sub(
        r"\s+",
        " ",
        t,
    ).strip()

    # -----------------------------------------------------
    # NORMALIZE VOICE VARIATIONS
    # -----------------------------------------------------

    replacements = {
        "you tube": "youtube",
        "you-tube": "youtube",
        "u tube": "youtube",
    }

    for old, new in replacements.items():

        t = t.replace(
            old,
            new,
        )

    t = re.sub(
        r"^(launch|start)\s+",
        "open ",
        t,
    )

    # Different ways of saying the same thing -> one command.
    # "press click", "tap", "left click" -> "click"
    t = normalize_command(
        t
    )

    # Safety also applies to the normalized command
    # (for example "visit system32" -> "open system32").
    if (
        is_blocked_request(t)
        and re.match(
            r"^(open|type|write|delete|run|click|press|go to)\b",
            t,
        )
    ):

        return "I won't access protected areas."

    # YouTube search / click first video
    yt_reply = youtube_step(t)

    if yt_reply is not None:
        return yt_reply

    # -----------------------------------------------------
    # TYPE
    # -----------------------------------------------------

    m = re.match(
        r"^(?:type|write)\s+(.+)$",
        orig,
        re.I,
    )

    if m:

        content = m.group(1).strip()

        mc = re.search(
            r"\s+in\s+(?:google\s+)?chrome$",
            content,
            re.I,
        )

        if mc:

            content = content[
                :mc.start()
            ].strip()

            subprocess.Popen(
                'start "" chrome',
                shell=True,
            )

            time.sleep(3)

        if is_blocked_request(content):

            return "I won't type that."

        pyautogui.write(
            content,
            interval=0.03,
        )

        return "Done."

    # -----------------------------------------------------
    # SCROLL
    # -----------------------------------------------------

    if t.startswith("scroll"):

        amount = 600

        if (
            "a lot" in t
            or "more" in t
            or "far" in t
        ):

            amount = 1500

        elif (
            "little" in t
            or "bit" in t
            or "slightly" in t
        ):

            amount = 200

        if re.search(
            r"\btop\b",
            t,
        ):

            pyautogui.press("home")

            return "Top."

        if re.search(
            r"\b(bottom|end)\b",
            t,
        ):

            pyautogui.press("end")

            return "Bottom."

        if re.search(
            r"\b(up|upward|upwards)\b",
            t,
        ):

            pyautogui.scroll(
                amount
            )

            return "Scrolling up."

        pyautogui.scroll(
            -amount
        )

        return "Scrolling down."

    # -----------------------------------------------------
    # PAGE UP / DOWN
    # -----------------------------------------------------

    if t == "page down":

        pyautogui.press(
            "pagedown"
        )

        return "Done."

    if t == "page up":

        pyautogui.press(
            "pageup"
        )

        return "Done."

    # -----------------------------------------------------
    # CLICK
    # -----------------------------------------------------

    if t in (
        "click",
        "left click",
    ):

        pyautogui.click()

        return "Clicked."

    if t == "double click":

        pyautogui.doubleClick()

        return "Double clicked."

    if t == "right click":

        pyautogui.rightClick()

        return "Right clicked."

    # "click send", "click the sign in button"
    m = re.match(
        r"^click\s+(?:on\s+)?(?:the\s+)?(.+)$",
        t,
    )

    if m:

        return click_by_name(
            m.group(1)
        )

    # -----------------------------------------------------
    # MOVE MOUSE
    # -----------------------------------------------------

    m = re.match(
        r"^move (?:the )?mouse "
        r"(left|right|up|down)"
        r"(?: (\d+))?$",
        t,
    )

    if m:

        d = int(
            m.group(2)
            or 200
        )

        dx, dy = {
            "left": (
                -d,
                0,
            ),
            "right": (
                d,
                0,
            ),
            "up": (
                0,
                -d,
            ),
            "down": (
                0,
                d,
            ),
        }[
            m.group(1)
        ]

        pyautogui.moveRel(
            dx,
            dy,
            duration=0.3,
        )

        return "Done."

    # -----------------------------------------------------
    # MOVE TO X Y
    # -----------------------------------------------------

    m = re.match(
        r"^move (?:the )?mouse "
        r"(?:to )?(\d+)\s+(\d+)$",
        t,
    )

    if m:

        x = int(
            m.group(1)
        )

        y = int(
            m.group(2)
        )

        pyautogui.moveTo(
            x,
            y,
            duration=0.3,
        )

        return "Done."

    # -----------------------------------------------------
    # PRESS KEY
    # -----------------------------------------------------

    m = re.match(
        r"^press (.+)$",
        t,
    )

    key_name = (
        m.group(1)
        if m
        else (
            t
            if t in KEYS
            else None
        )
    )

    if key_name is not None:

        if key_name in KEYS:

            pyautogui.press(
                KEYS[key_name]
            )

            return "Done."

        if m:

            return (
                "I don't know that key."
            )

    # -----------------------------------------------------
    # SHORTCUT
    # -----------------------------------------------------

    if t in SHORTCUTS:

        pyautogui.hotkey(
            *SHORTCUTS[t]
        )

        return "Done."

    # -----------------------------------------------------
    # OPEN APP / WEBSITE
    # -----------------------------------------------------

    if t.startswith("open "):

        name = t[5:].strip()

        name = re.sub(
            r"^(the|my|up)\s+",
            "",
            name,
        )

        name = re.sub(
            r"\s+(app|application|browser|please)$",
            "",
            name,
        )

        # ---------------------------------------------
        # OPEN YOUTUBE IN CHROME
        # ---------------------------------------------

        if re.search(
            r"\b(?:youtube|yt)\b.*\bchrome\b",
            name,
        ):

            subprocess.Popen(
                'start "" chrome '
                '"https://www.youtube.com"',
                shell=True,
            )

            return "Opening YouTube."

        # ---------------------------------------------
        # OPEN YOUTUBE
        # ---------------------------------------------

        if (
            "youtube" in name
            or name == "yt"
        ):

            subprocess.Popen(
                'start "" chrome '
                '"https://www.youtube.com"',
                shell=True,
            )

            return "Opening YouTube."

        known = (
            list(APPS)
            + list(SITES)
        )

        match = (
            name
            if name in known
            else None
        )

        if match is None:

            close = difflib.get_close_matches(
                name,
                known,
                n=1,
                cutoff=0.65,
            )

            match = (
                close[0]
                if close
                else None
            )

        if match in APPS:

            subprocess.Popen(
                'start "" '
                + APPS[match],
                shell=True,
            )

            return (
                "Opening "
                + match
                + "."
            )

        if match in SITES:

            if match in (
                "youtube",
                "yt",
            ):

                subprocess.Popen(
                    'start "" chrome '
                    '"'
                    + SITES[match]
                    + '"',
                    shell=True,
                )

            else:

                webbrowser.open(
                    SITES[match]
                )

            return (
                "Opening "
                + match
                + "."
            )

        return (
            "I don't know that app."
        )

    # -----------------------------------------------------
    # SEARCH
    # -----------------------------------------------------

    for prefix in (
        "search for ",
        "search ",
        "google ",
    ):

        if t.startswith(prefix):

            query = t[
                len(prefix):
            ].strip()

            if not query:

                return (
                    "What should I search?"
                )

            webbrowser.open(
                "https://www.google.com/search?q="
                + query.replace(
                    " ",
                    "+",
                )
            )

            return "Searching."

    # -----------------------------------------------------
    # CPU
    # -----------------------------------------------------

    if re.search(
        r"\bcpu\b",
        t,
    ):

        return (
            "CPU is "
            + str(
                psutil.cpu_percent(
                    interval=1
                )
            )
            + " percent."
        )

    # -----------------------------------------------------
    # RAM
    # -----------------------------------------------------

    if (
        re.search(
            r"\bram\b",
            t,
        )
        or "memory usage" in t
    ):

        m2 = psutil.virtual_memory()

        return (
            "RAM is "
            + str(m2.percent)
            + " percent used."
        )

    # -----------------------------------------------------
    # BATTERY
    # -----------------------------------------------------

    if "battery" in t:

        b = psutil.sensors_battery()

        if b is None:

            return "No battery found."

        return (
            "Battery is "
            + str(
                int(b.percent)
            )
            + " percent."
        )

    # -----------------------------------------------------
    # DISK
    # -----------------------------------------------------

    if re.search(
        r"\b(disk|storage)\b",
        t,
    ):

        d = psutil.disk_usage(
            "C:\\"
        )

        return (
            str(
                round(
                    d.free
                    / 1024**3
                )
            )
            + " GB free on C."
        )

    # -----------------------------------------------------
    # SYSTEM STATUS
    # -----------------------------------------------------

    if t in (
        "system status",
        "pc status",
        "pc health",
    ):

        m2 = psutil.virtual_memory()

        return (
            "CPU "
            + str(
                psutil.cpu_percent(
                    interval=1
                )
            )
            + "%, RAM "
            + str(
                m2.percent
            )
            + "%."
        )

    # -----------------------------------------------------
    # TIME
    # -----------------------------------------------------

    if t in (
        "time",
        "what time is it",
        "what is the time",
        "whats the time",
        "what s the time",
    ):

        return (
            "It's "
            + datetime.datetime.now().strftime(
                "%I:%M %p"
            )
            + "."
        )

    return None


# =========================================================
# RUN MULTIPLE COMMANDS
# =========================================================

def run_command(text):

    steps = STEP_SPLIT.split(
        text.strip()
    )

    replies = []

    for i, step in enumerate(steps):

        step = step.strip()

        if not step:
            continue

        reply = run_step(step)

        if reply is None:

            if len(steps) == 1:
                return None

            reply = (
                "I didn't understand "
                + step
                + "."
            )

        replies.append(reply)

        if (
            reply.startswith("Opening")
            and i < len(steps) - 1
        ):

            time.sleep(2)

    if not replies:
        return None

    return " ".join(replies)


# =========================================================
# EXTRA FEATURES
# rock paper scissors, local memory, tasks, reminders,
# volume, dictation, clipboard helper, quiz
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEM_FILE = os.path.join(BASE_DIR, "sink_memory.json")
TASK_FILE = os.path.join(BASE_DIR, "sink_tasks.json")


def _json_load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _json_save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as error:
        print("SAVE ERROR:", error)


def _item_word(n):
    return "item" if n == 1 else "items"


def mem_load():
    return _json_load(MEM_FILE)


def mem_add(fact):
    facts = mem_load()
    facts.append(fact)
    _json_save(MEM_FILE, facts)


def speak_async(text):
    """Speak from a background thread (used by reminders)."""

    def _run():
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass

            engine = pyttsx3.init()
            engine.setProperty("rate", 190)
            engine.setProperty("volume", 1.0)
            engine.say(clean_for_speech(text))
            engine.runAndWait()
        except Exception as error:
            print("REMINDER VOICE ERROR:", error)

    threading.Thread(target=_run, daemon=True).start()


SCREEN_WIDTH = 1280   # lower to 1024 or 768 if vision replies feel slow

SOLVE_NOTE = (
    "\n\n----------------------------------------\n"
    "Note: this solution was written by an AI assistant (SINK) "
    "and can contain mistakes. Please check it once before "
    "you submit or rely on it.\n"
)

HELP_TEXT = (
    "You can say: open Chrome. Scroll down. Click. "
    "Click, then the name of a button, like click Send. "
    "Type something. Search something. "
    "What's on my screen. Explain this error. "
    "Solve what I copied, or say solve what I copied in Notepad. "
    "Start dictation. "
    "Weather in Bhopal. Air quality in Bhopal. "
    "Can I go outside in Bhopal. Latest news. "
    "Wikipedia, then a topic. "
    "Are you online. "
    "Teach me a skill: learn study mode, search lo-fi music on YouTube, "
    "click first video. "
    "Then just say study mode. List my skills. "
    "You can also say longer things, like open YouTube and play lo-fi music. "
    "Remind me in ten minutes. Read my clipboard. "
    "Stop listening."
)


SOLVE_PROMPT_TEXT = (
    "You are a careful tutor. Solve every question in the text. "
    "For each question write 'Question N', then the working in short "
    "numbered steps, then a line that starts exactly with "
    "'FINAL ANSWER:' followed by the answer. "
    "Write maths in PLAIN TEXT only: use symbols like \u221a, \u00b2, "
    "\u00b3, \u00d7, \u00f7, \u03c0 and write fractions like (a)/(b). "
    "Do NOT use LaTeX, dollar signs, backslashes or markdown. "
    "If the text contains no questions or problems to solve, reply "
    "with exactly NO_QUESTIONS and nothing else."
)

SOLVE_PROMPT_PAGE = (
    "You are a careful tutor. Solve every question in the text. "
    "For each question write 'Question N', then the working in short "
    "numbered steps, then a line that starts exactly with "
    "'FINAL ANSWER:' followed by the answer. "
    "Write maths in LaTeX: $...$ for inline maths and $$...$$ for "
    "displayed equations. Do not use markdown headings or tables. "
    "If the text contains no questions or problems to solve, reply "
    "with exactly NO_QUESTIONS and nothing else."
)

_SUP = dict(zip("0123456789+-n()", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207f\u207d\u207e"))
_SUB = dict(zip("0123456789", "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"))

_LATEX_SYMBOLS = {
    "times": "\u00d7", "cdot": "\u00b7", "div": "\u00f7",
    "pm": "\u00b1", "mp": "\u2213", "pi": "\u03c0",
    "theta": "\u03b8", "alpha": "\u03b1", "beta": "\u03b2",
    "gamma": "\u03b3", "delta": "\u03b4", "lambda": "\u03bb",
    "mu": "\u03bc", "sigma": "\u03c3", "omega": "\u03c9",
    "Delta": "\u0394", "infty": "\u221e", "leq": "\u2264",
    "le": "\u2264", "geq": "\u2265", "ge": "\u2265",
    "neq": "\u2260", "ne": "\u2260", "approx": "\u2248",
    "rightarrow": "\u2192", "to": "\u2192", "Rightarrow": "\u21d2",
    "implies": "\u21d2", "sum": "\u2211", "int": "\u222b",
    "ldots": "...", "dots": "...", "cdots": "...",
    "quad": " ", "qquad": "  ", "left": "", "right": "",
}


def _to_script(content, table, mark):
    if content and all(c in table for c in content):
        return "".join(table[c] for c in content)

    return mark + "(" + content + ")"


def latex_to_text(s):
    """Turn LaTeX / markdown into readable plain text (for Notepad)."""

    s = re.sub(r"\$\$(.*?)\$\$", r"\1", s, flags=re.S)
    s = re.sub(r"\\\[(.*?)\\\]", r"\1", s, flags=re.S)
    s = re.sub(r"\\\((.*?)\\\)", r"\1", s, flags=re.S)
    s = re.sub(r"\$([^$\n]+)\$", r"\1", s)

    s = s.replace("^\\circ", "\u00b0").replace("\\degree", "\u00b0")

    for _ in range(4):
        s = re.sub(
            r"\\(?:text|textbf|mathrm|mathbf|boxed|mathit)\{([^{}]*)\}",
            r"\1", s,
        )
        s = re.sub(
            r"\\sqrt\[([^\]]*)\]\{([^{}]*)\}", r"(\2)^(1/\1)", s,
        )
        s = re.sub(r"\\sqrt\{([^{}]*)\}", "\u221a(\\1)", s)
        s = re.sub(
            r"\^\{([^{}]*)\}",
            lambda m: _to_script(m.group(1), _SUP, "^"), s,
        )
        s = re.sub(
            r"_\{([^{}]*)\}",
            lambda m: _to_script(m.group(1), _SUB, "_"), s,
        )
        s = re.sub(
            r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s,
        )

    s = re.sub(
        r"\^\{([^{}]*)\}",
        lambda m: _to_script(m.group(1), _SUP, "^"), s,
    )
    s = re.sub(
        r"\^([0-9n])", lambda m: _SUP[m.group(1)], s,
    )
    s = re.sub(
        r"_\{([^{}]*)\}",
        lambda m: _to_script(m.group(1), _SUB, "_"), s,
    )
    s = re.sub(
        r"_([0-9])", lambda m: _SUB[m.group(1)], s,
    )

    s = re.sub(
        r"\\([a-zA-Z]+)(?![a-zA-Z])",
        lambda m: _LATEX_SYMBOLS.get(m.group(1), m.group(1)),
        s,
    )

    s = re.sub(r"\\[,;:! ]", " ", s)
    s = s.replace("\\\\", "\n")
    s = s.replace("{", "").replace("}", "")

    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = s.replace("`", "")
    s = re.sub(r"[ \t]+\n", "\n", s)

    return s


def extract_finals(answer):
    """Collect the text after every 'FINAL ANSWER:' line."""
    finals = []

    for line in answer.splitlines():

        m = re.match(
            r"^[\s*#>_`]*final\s+answers?[\s*:_`\-]*(.+)$",
            line.strip(),
            re.I,
        )

        if m and m.group(1).strip(" *_`"):
            finals.append(m.group(1).strip(" *_`"))

    return finals


def build_notepad_text(answer):
    finals = extract_finals(answer)
    body = latex_to_text(answer).strip()

    if not finals:
        return body

    lines = ["FINAL ANSWERS", ""]

    for i, f in enumerate(finals, 1):
        lines.append(str(i) + ") " + latex_to_text(f).strip())

    lines += [
        "",
        "=" * 40,
        "FULL SOLUTIONS",
        "=" * 40,
        "",
        body,
    ]

    return "\n".join(lines)


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SINK Solution</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet"
 href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer
 src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer
 src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
 onload="renderMathInElement(document.body,{delimiters:[
  {left:'$$',right:'$$',display:true},
  {left:'\\[',right:'\\]',display:true},
  {left:'$',right:'$',display:false},
  {left:'\\(',right:'\\)',display:false}],throwOnError:false});"></script>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;background:#0f0d08;
  color:#fff3d1;margin:0;padding:32px;line-height:1.7;font-size:20px}
 .wrap{max-width:860px;margin:0 auto}
 h1{color:#ffd76a;letter-spacing:6px;font-size:26px}
 h2{color:#ffd76a;border-bottom:1px solid #4a3a12;padding-bottom:6px;
  margin-top:34px;font-size:22px}
 p{margin:6px 0}
 .summary{background:#17130a;border:2px solid #d9a92e;border-radius:12px;
  padding:14px 20px;margin:18px 0}
 .summary h3{margin:0 0 8px 0;color:#7cffb2}
 .summary li{margin:6px 0}
 .final{background:#10261a;border-left:5px solid #7cffb2;
  padding:8px 14px;margin:10px 0;border-radius:6px}
 .note{margin-top:40px;color:#b8a979;font-size:15px;
  border-top:1px solid #30260e;padding-top:12px}
 .katex-display{overflow-x:auto;overflow-y:hidden}
</style>
</head>
<body><div class="wrap">
<h1>S I N K &nbsp; S O L U T I O N</h1>
__SUMMARY__
__BODY__
<div class="note">Written by an AI assistant (SINK). It can make
mistakes. Please check it once before you submit or rely on it.</div>
</div></body></html>
"""


def build_solution_html(answer):
    import html as _html

    finals = extract_finals(answer)

    # keep display-math blocks on one line so they render
    answer = re.sub(
        r"\$\$(.*?)\$\$",
        lambda m: "$$" + " ".join(m.group(1).split()) + "$$",
        answer,
        flags=re.S,
    )
    answer = re.sub(
        r"\\\[(.*?)\\\]",
        lambda m: "\\[" + " ".join(m.group(1).split()) + "\\]",
        answer,
        flags=re.S,
    )

    def fmt(line):
        line = _html.escape(line, quote=False)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)

    parts = []

    for raw in answer.splitlines():

        line = raw.strip()

        if not line:
            continue

        m = re.match(
            r"^[\s*#>_`]*final\s+answers?[\s*:_`\-]*(.+)$",
            line,
            re.I,
        )

        if m and m.group(1).strip(" *_`"):
            parts.append(
                '<div class="final"><b>Final answer:</b> '
                + fmt(m.group(1).strip(" *_`"))
                + "</div>"
            )
        elif re.match(r"^[\s*#]*(?:question|q)\s*\d+", line, re.I):
            parts.append(
                "<h2>" + fmt(line.strip("#* ")) + "</h2>"
            )
        else:
            parts.append("<p>" + fmt(line.lstrip("#").strip()) + "</p>")

    summary = ""

    if finals:
        items = "".join(
            "<li>" + fmt(f) + "</li>" for f in finals
        )
        summary = (
            '<div class="summary"><h3>FINAL ANSWERS</h3><ol>'
            + items
            + "</ol></div>"
        )

    return (
        _PAGE_TEMPLATE
        .replace("__SUMMARY__", summary)
        .replace("__BODY__", "\n".join(parts))
    )


def set_clipboard(text):
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
            input=text,
            text=True,
            timeout=10,
        )
        return out.returncode == 0
    except Exception:
        return False


def screen_to_b64():
    """Screenshot -> small JPEG -> base64 (for the vision model)."""
    img = pyautogui.screenshot().convert("RGB")

    if img.width > SCREEN_WIDTH:
        h = int(SCREEN_WIDTH * img.height / img.width)
        img = img.resize((SCREEN_WIDTH, h))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)

    return base64.b64encode(buf.getvalue()).decode()


def click_by_name(name):
    """Click a button/link by its NAME using Windows UI Automation.
    Needs:  pip install pywinauto"""

    name = re.sub(
        r"\s+(?:button|link|tab|icon|option|menu)$",
        "",
        name.strip().lower(),
    )

    if not name:
        return "Click what?"

    try:
        import win32gui
        from pywinauto import Desktop
    except Exception:
        return "Click by name needs pywinauto installed."

    try:
        hwnd = win32gui.GetForegroundWindow()
        win = Desktop(backend="uia").window(handle=hwnd)

        found = {}

        for el in win.descendants():
            try:
                label = el.window_text().strip().lower()
                kind = el.element_info.control_type

                if (
                    label
                    and kind in (
                        "Button", "Hyperlink", "MenuItem", "TabItem",
                        "ListItem", "CheckBox", "RadioButton", "Edit",
                        "SplitButton",
                    )
                    and el.is_visible()
                ):
                    found.setdefault(label, el)
            except Exception:
                pass

        if not found:
            return "I can't see any buttons here."

        match = None

        if name in found:
            match = name
        else:
            contains = [k for k in found if name in k]

            if contains:
                match = min(contains, key=len)
            else:
                close = difflib.get_close_matches(
                    name, list(found), n=1, cutoff=0.6,
                )
                match = close[0] if close else None

        if match is None:
            return "I can't find " + name + " on screen."

        found[match].click_input()

        return "Clicked " + match + "."

    except Exception as error:
        print("CLICK BY NAME ERROR:", error)
        return "I couldn't click that."


def get_clipboard():
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------
# NUMBERS (voice gives both "10" and "ten")
# ---------------------------------------------------------

NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60,
}


def to_number(s):
    s = s.strip().lower().replace("-", " ")

    try:
        return float(s)
    except ValueError:
        pass

    words = s.split()

    if words and all(w in NUM_WORDS for w in words):
        return float(sum(NUM_WORDS[w] for w in words))

    return None


UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600,
}

UNIT_RE = r"(seconds?|secs?|minutes?|mins?|hours?)"


def fire_reminder(message):
    print("REMINDER:", message)

    try:
        import winsound
        winsound.Beep(1000, 300)
    except Exception:
        pass

    speak_async(message)


def set_reminder(amount, unit, message):
    n = to_number(amount)

    if n is None or n <= 0:
        return None

    seconds = n * UNIT_SECONDS[unit.lower()]

    if seconds > 24 * 3600:
        return "That's too long. I can set up to 24 hours."

    timer = threading.Timer(seconds, fire_reminder, [message])
    timer.daemon = True
    timer.start()

    pretty = (
        str(int(n)) if float(n).is_integer() else str(n)
    ) + " " + unit.lower()

    return pretty


# ---------------------------------------------------------
# SESSIONS (no wake word needed while active)
# ---------------------------------------------------------

RPS = {"on": False, "you": 0, "sink": 0}
DICTATION = {"on": False}

RPS_BEATS = {
    ("rock", "scissors"),
    ("paper", "rock"),
    ("scissors", "paper"),
}

DICTATION_JUNK = {
    "thank you", "thanks for watching", "you", "bye",
    "thanks", "okay", "ok", "hmm",
}


def session_active():
    return RPS["on"] or DICTATION["on"]


def rps_command(t):
    clean = re.sub(r"[^\w\s']", " ", t)
    clean = re.sub(r"\s+", " ", clean).strip()

    has_all = (
        re.search(r"\b(rock|stone)\b", clean)
        and re.search(r"\bpaper\b", clean)
        and re.search(r"\bscissors?\b", clean)
    )

    if has_all and re.search(r"\b(play|start|lets|let's)\b", clean):
        RPS.update(on=True, you=0, sink=0)
        return (
            "Game on! Say rock, paper or scissors. "
            "Say stop game when you're done."
        )

    if not RPS["on"]:
        return None

    if re.search(r"\b(stop|end|quit|exit)\b.*\bgame\b", clean):
        RPS["on"] = False
        you, sink = RPS["you"], RPS["sink"]

        if you > sink:
            verdict = "You win overall!"
        elif sink > you:
            verdict = "I win overall!"
        else:
            verdict = "It's a tie overall."

        return f"Game over. You {you}, me {sink}. {verdict}"

    m = re.search(r"\b(rock|stone|paper|scissors?)\b", clean)

    if not m or has_all:
        return None

    word = m.group(1)
    you = {"stone": "rock", "scissor": "scissors"}.get(word, word)
    sink = random.choice(["rock", "paper", "scissors"])

    if you == sink:
        result = "Draw!"
    elif (you, sink) in RPS_BEATS:
        RPS["you"] += 1
        result = "You win!"
    else:
        RPS["sink"] += 1
        result = "I win!"

    return (
        f"You chose {you}, I chose {sink}. {result} "
        f"Score: you {RPS['you']}, me {RPS['sink']}."
    )


# ---------------------------------------------------------
# MAIN EXTRA COMMAND ROUTER
#
# Returns:
#   None                      -> not an extra command
#   ("say", reply)            -> reply directly
#   ("ask_ai", prompt, tokens)-> send prompt to LM Studio
# ---------------------------------------------------------

# =========================================================
# ONLINE FEATURES + NEBIUS (NVIDIA Nemotron) HYBRID BRAIN
#
# Everything here is OPTIONAL. If there is no internet, SINK
# keeps working with LM Studio + local Whisper like before.
# Settings live in a .env file next to gui.py (see .env.example).
# =========================================================

import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def _load_env():
    path = os.path.join(BASE_DIR, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(
                    key.strip(),
                    value.strip().strip('"').strip("'"),
                )
    except FileNotFoundError:
        pass
    except Exception as error:
        print("ENV ERROR:", error)


_load_env()

NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "")
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "")
NEBIUS_MODEL = os.environ.get("NEBIUS_MODEL", "")
# Optional: a small fast model for chat/planning and a big one for
# hard reasoning (solve). Both fall back to NEBIUS_MODEL.
NEBIUS_MODEL_FAST = os.environ.get("NEBIUS_MODEL_FAST", "") or NEBIUS_MODEL
NEBIUS_MODEL_HEAVY = (
    os.environ.get("NEBIUS_MODEL_HEAVY", "")
    or NEBIUS_MODEL
    or NEBIUS_MODEL_FAST
)
USE_NEBIUS = os.environ.get("USE_NEBIUS", "1") != "0"
DEFAULT_CITY = os.environ.get("SINK_DEFAULT_CITY", "")

OFFLINE_MSG = (
    "I'm offline right now, so I can't check that. "
    "Everything else still works."
)

_NET = {"t": 0.0, "ok": False}
_CLOUD = {"client": None}


def internet_ok():
    # Cached for 20 seconds so voice commands stay fast.
    now = time.time()

    if now - _NET["t"] < 20:
        return _NET["ok"]

    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        ok = True
    except OSError:
        ok = False

    _NET["t"] = now
    _NET["ok"] = ok

    return ok


def cloud_ready():
    return bool(
        USE_NEBIUS
        and PROVIDERS
        and internet_ok()
    )


def cloud_client():
    if _CLOUD["client"] is None:
        _CLOUD["client"] = OpenAI(
            base_url=NEBIUS_BASE_URL,
            api_key=NEBIUS_API_KEY,
            max_retries=0,
            timeout=25,
        )

    return _CLOUD["client"]


# =========================================================
# ONLINE BRAINS: Groq + Gemini (+ Nebius if still configured)
#
# Everyday chat / planning : Groq first (fast), then Gemini.
# Hard "solve" questions   : Gemini first (smarter), then Groq.
# If one fails or hits its free limit it is skipped for a minute,
# the next one is tried, and finally the local LM Studio model.
#
# .env lines (all optional; set USE_NEBIUS=0 to turn online off):
#   GROQ_API_KEY=...
#   GROQ_MODEL=...            (GROQ_MODEL_HEAVY optional)
#   GEMINI_API_KEY=...
#   GEMINI_MODEL=...          (GEMINI_MODEL_HEAVY optional)
# =========================================================

PROVIDER_COOLDOWN = 60  # seconds to skip a provider after a failure
_PROV_DOWN = {}
PROVIDERS = []


def _add_provider(name, key, url, fast, heavy="", extra_tokens=0):

    if key and url and fast:

        PROVIDERS.append({
            "name": name,
            "key": key,
            "url": url,
            "fast": fast,
            "heavy": heavy or fast,
            "extra_tokens": extra_tokens,
            "client": None,
        })


_add_provider(
    "groq",
    os.environ.get("GROQ_API_KEY", ""),
    "https://api.groq.com/openai/v1",
    os.environ.get("GROQ_MODEL", ""),
    os.environ.get("GROQ_MODEL_HEAVY", ""),
)

# Gemini "thinking" models spend part of max_tokens on hidden
# thinking, so they get extra headroom to still return an answer.
_add_provider(
    "gemini",
    os.environ.get("GEMINI_API_KEY", ""),
    "https://generativelanguage.googleapis.com/v1beta/openai/",
    os.environ.get("GEMINI_MODEL", ""),
    os.environ.get("GEMINI_MODEL_HEAVY", ""),
    extra_tokens=600,
)

# Old Nebius settings keep working if they are still in .env.
_add_provider(
    "nebius",
    NEBIUS_API_KEY,
    NEBIUS_BASE_URL,
    NEBIUS_MODEL_FAST,
    NEBIUS_MODEL_HEAVY,
)


def online_brain_name():

    names = {
        "groq": "Groq",
        "gemini": "Gemini",
        "nebius": "Nebius",
    }

    return " and ".join(
        names[p["name"]] for p in PROVIDERS
    )


def cloud_chat(messages, max_tokens, temperature, heavy=False):

    """Try the online brains in order. Returns the first good
    result, or None so the caller can use the local model."""

    order = (
        ("gemini", "groq", "nebius")
        if heavy
        else ("groq", "gemini", "nebius")
    )

    for name in order:

        for p in PROVIDERS:

            if p["name"] != name:
                continue

            if time.time() < _PROV_DOWN.get(name, 0):
                continue

            model = p["heavy"] if heavy else p["fast"]

            try:

                if p["client"] is None:
                    p["client"] = OpenAI(
                        base_url=p["url"],
                        api_key=p["key"],
                        max_retries=0,
                        timeout=25,
                    )

                result = p["client"].chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens + p["extra_tokens"],
                    temperature=temperature,
                )

                content = result.choices[0].message.content

                if content and content.strip():
                    print("AI BACKEND:", name, "/", model)
                    return result

                print(name.upper() + ": empty answer, trying next.")

            except Exception as error:

                print(name.upper() + " FAILED:", error)

                _PROV_DOWN[name] = time.time() + PROVIDER_COOLDOWN

    return None


def _http_get(url, params=None, timeout=6):
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SINK-voice-assistant/1.0"},
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


_WMO = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    85: "snow showers",
    86: "snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}


def get_weather(city):
    # Only the city name leaves the PC. No voice, no chat.
    if not internet_ok():
        return OFFLINE_MSG

    try:
        geo = json.loads(
            _http_get(
                "https://geocoding-api.open-meteo.com/v1/search",
                {"name": city, "count": 1},
            )
        )

        place = geo["results"][0]

        data = json.loads(
            _http_get(
                "https://api.open-meteo.com/v1/forecast",
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current_weather": "true",
                },
            )
        )["current_weather"]

        sky = _WMO.get(int(data.get("weathercode", -1)), "")
        temp = round(float(data["temperature"]))
        wind = round(float(data["windspeed"]))

        text = f"In {place['name']} it is {temp} degrees Celsius"

        if sky:
            text += f", {sky}"

        text += f". Wind is {wind} kilometres per hour."

        return text

    except IndexError:
        return f"I couldn't find a place called {city}."
    except Exception as error:
        print("WEATHER ERROR:", error)
        return "I couldn't get the weather right now."


def _geocode(city):
    geo = json.loads(
        _http_get(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": city, "count": 1},
        )
    )

    return geo["results"][0]


def _aqi_level(aqi):
    # US AQI scale
    if aqi <= 50:
        return "good"
    if aqi <= 100:
        return "moderate"
    if aqi <= 150:
        return "unhealthy for sensitive people"
    if aqi <= 200:
        return "unhealthy"
    if aqi <= 300:
        return "very unhealthy"
    return "hazardous"


def _get_air(place):
    data = json.loads(
        _http_get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "us_aqi,pm2_5",
            },
        )
    )["current"]

    aqi = data.get("us_aqi")

    return None if aqi is None else round(float(aqi))


def get_air_quality(city):
    if not internet_ok():
        return OFFLINE_MSG

    try:
        place = _geocode(city)
        aqi = _get_air(place)

        if aqi is None:
            return f"I couldn't get air quality data for {place['name']}."

        return (
            f"Air quality in {place['name']} is {_aqi_level(aqi)}. "
            f"The AQI is {aqi} on the US scale."
        )

    except IndexError:
        return f"I couldn't find a place called {city}."
    except Exception as error:
        print("AQI ERROR:", error)
        return "I couldn't get the air quality right now."


def go_out_advice(city):
    # Weather + air quality -> a simple "is it okay to go out" answer.
    if not internet_ok():
        return OFFLINE_MSG

    try:
        place = _geocode(city)

        w = json.loads(
            _http_get(
                "https://api.open-meteo.com/v1/forecast",
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current_weather": "true",
                },
            )
        )["current_weather"]

        try:
            aqi = _get_air(place)
        except Exception as error:
            print("AQI ERROR:", error)
            aqi = None

        code = int(w.get("weathercode", -1))
        temp = round(float(w["temperature"]))
        wind = round(float(w["windspeed"]))
        sky = _WMO.get(code, "")

        problems = []
        tips = []

        if code >= 95:
            problems.append("there is a thunderstorm")
        elif 51 <= code <= 67 or 80 <= code <= 82:
            tips.append("it is raining, so take an umbrella")
        elif 71 <= code <= 77 or 85 <= code <= 86:
            tips.append("it is snowing, so dress warmly")

        if temp >= 40:
            problems.append("it is extremely hot")
        elif temp >= 35:
            tips.append("it is hot, so drink water and avoid the sun")
        elif temp <= 5:
            tips.append("it is cold, so wear warm clothes")

        if wind >= 50:
            problems.append("the wind is very strong")

        if aqi is not None:
            if aqi > 150:
                problems.append(
                    f"the air is {_aqi_level(aqi)} with an AQI of {aqi}"
                )
            elif aqi > 100:
                tips.append(
                    "the air is unhealthy for sensitive people, "
                    "so a mask can help"
                )

        summary = f"In {place['name']} it is {temp} degrees Celsius"

        if sky:
            summary += f", {sky}"

        summary += "."

        if aqi is not None:
            summary += f" AQI is {aqi}, {_aqi_level(aqi)}."

        if problems:
            verdict = (
                " It is better to stay inside right now, because "
                + " and ".join(problems)
                + "."
            )
        else:
            verdict = " It looks fine to go out."

            if tips:
                verdict += " Just note that " + " and ".join(tips) + "."

        return summary + verdict

    except IndexError:
        return f"I couldn't find a place called {city}."
    except Exception as error:
        print("GO OUT ERROR:", error)
        return "I couldn't check that right now."


def get_news(count=4):
    if not internet_ok():
        return OFFLINE_MSG

    try:
        xml_data = _http_get(
            "https://news.google.com/rss",
            {"hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
        )

        root = ET.fromstring(xml_data)
        titles = []

        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()

            # Google News adds " - Source name" at the end.
            title = re.sub(r"\s+-\s+[^-]+$", "", title)

            if title:
                titles.append(title)

            if len(titles) >= count:
                break

        if not titles:
            return "I couldn't find any news right now."

        return "Top headlines. " + " ... ".join(
            f"{i}. {t}" for i, t in enumerate(titles, 1)
        )

    except Exception as error:
        print("NEWS ERROR:", error)
        return "I couldn't get the news right now."


def get_wiki(topic):
    if not internet_ok():
        return OFFLINE_MSG

    try:
        title = urllib.parse.quote(topic.strip().replace(" ", "_"))

        data = json.loads(
            _http_get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/"
                + title
            )
        )

        extract = (data.get("extract") or "").strip()

        if not extract:
            return f"I couldn't find {topic} on Wikipedia."

        # Keep it short for voice: first two sentences.
        sentences = re.split(r"(?<=[.!?])\s+", extract)

        return " ".join(sentences[:2])

    except Exception as error:
        print("WIKI ERROR:", error)
        return f"I couldn't find {topic} on Wikipedia."


ACTION_HINT = re.compile(
    r"\b(open|launch|go to|search|play|watch|listen|find|look up|"
    r"type|write|click|press|scroll|close|switch|visit|"
    r"minimi[sz]e|maximi[sz]e)\b",
    re.I,
)

ALLOWED_STEP = re.compile(
    r"^(open|go to|search|type|click|double click|right click|"
    r"press|scroll|move mouse)\b",
    re.I,
)

DANGEROUS_KEYS = re.compile(
    r"alt\s*\+?\s*f4|win(?:dows)?\s*\+?\s*r\b|"
    r"ctrl\s*\+?\s*alt\s*\+?\s*del",
    re.I,
)

PLAN_PROMPT = (
    "You turn a spoken request into simple desktop commands for a "
    "voice assistant. Use ONLY these command forms: "
    "open <app or website>; go to <website>; search <text>; "
    "search <text> on youtube; click first video; "
    "type <text>; click; click <button name>; double click; "
    "right click; press <key>; scroll up; scroll down; "
    "move mouse <left|right|up|down> <pixels>. "
    "Reply with ONLY JSON like {\"steps\": "
    "[\"search lo-fi music on youtube\", \"click first video\"]}. "
    "Use at most 6 steps, in order. "
    "If the request is a question or normal chat and not a computer "
    "action, reply {\"steps\": []}. Never include shutdown, restart, "
    "delete, passwords, tokens or system files."
)


def plan_steps(text):
    # Nemotron (fast model) breaks a free-form request into steps
    # that SINK's own run_step() already knows how to do.
    try:
        result = cloud_chat(
            [
                {"role": "system", "content": PLAN_PROMPT},
                {"role": "user", "content": text},
            ],
            400,
            0.1,
        )

        if result is None:
            return []

        raw = result.choices[0].message.content or ""
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I)

        found = re.search(r"\{.*\}", raw, re.S)

        if not found:
            return []

        data = json.loads(found.group(0))

        steps = [
            str(s).strip()
            for s in data.get("steps", [])
            if str(s).strip()
        ]

        return steps[:6]

    except Exception as error:
        print("PLAN ERROR:", error)
        return []


# =========================================================
# TEACHABLE SKILLS
# Say: "learn study mode: open YouTube, search lo-fi music"
# Later say: "study mode"  -> SINK runs all the steps.
# Skills live in sink_skills.json (plain, local, editable).
# =========================================================

SKILL_FILE = os.path.join(BASE_DIR, "sink_skills.json")
MAX_SKILLS = 30
MAX_SKILL_STEPS = 8

SKILL_STEP_OK = re.compile(
    r"^(open|go to|search|google|type|write|click|double click|"
    r"right click|left click|tap|hit|press|scroll|move mouse|copy|"
    r"paste|cut|select all|close|switch|go back|go forward|new tab|"
    r"next tab|refresh|reload|zoom|maximi[sz]e|minimi[sz]e|undo|"
    r"redo|enter)\b",
    re.I,
)

RESERVED_SKILL_NAMES = {
    "stop", "help", "sleep", "go to sleep", "stop listening",
    "dictation", "memory", "skills", "yes", "no",
}


def skills_load():
    data = _json_load(SKILL_FILE)

    return [
        s for s in data
        if isinstance(s, dict)
        and s.get("name")
        and isinstance(s.get("steps"), list)
    ]


def skills_save(skills):
    _json_save(SKILL_FILE, skills)


def _skill_name(name):
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())

    return re.sub(r"\s+", " ", name).strip()


def find_skill(name, skills):
    name = _skill_name(name)

    if not name:
        return None

    for s in skills:
        if s["name"] == name:
            return s

    close = difflib.get_close_matches(
        name,
        [s["name"] for s in skills],
        n=1,
        cutoff=0.85,
    )

    if close:
        for s in skills:
            if s["name"] == close[0]:
                return s

    return None


def split_skill_steps(text):
    steps = []

    for part in re.split(r"\s*,\s*", text.strip(" .!?")):
        for s in STEP_SPLIT.split(part.strip()):
            s = s.strip(" .!?,")

            if s:
                steps.append(s.lower())

    return steps


def check_skill_steps(steps):
    # Returns (steps, None) if OK, else (None, error message).
    if not steps:
        return None, "I didn't hear any steps."

    if len(steps) > MAX_SKILL_STEPS:
        return None, (
            f"A skill can have at most {MAX_SKILL_STEPS} steps."
        )

    for step in steps:

        if is_blocked_request(step) or DANGEROUS_KEYS.search(step):
            return None, (
                "I won't save that, it touches protected areas."
            )

        if not SKILL_STEP_OK.match(step):
            return None, (
                f"I can't save the step {step}. A step should start "
                "with open, search, type, click, press or scroll."
            )

    return steps, None


def run_skill(skill, focus_cb=None):
    steps = skill["steps"]

    if focus_cb and needs_focus(" and ".join(steps)):
        focus_cb()

    failed = []

    for i, step in enumerate(steps):

        # Check again at run time: the file can be edited by hand.
        if is_blocked_request(step) or DANGEROUS_KEYS.search(step):
            failed.append(step)
            continue

        reply = run_step(step)

        if reply is None:
            failed.append(step)
            continue

        if reply.startswith("Opening") and i < len(steps) - 1:
            time.sleep(2.5)

    msg = f"{skill['name']} done."

    if failed:
        msg += " I couldn't do: " + ", ".join(failed) + "."

    return msg


def _teach_skill(name, steps_text, explicit):
    name = _skill_name(name)

    steps, error = check_skill_steps(split_skill_steps(steps_text))

    if error:
        # "learn python, how do I start" is probably just chat, so stay
        # quiet for that case. Safety and size problems are always said.
        chatty = error.startswith(("I can't save the step", "I didn't hear"))

        return None if (chatty and not explicit) else error

    if (
        not name
        or len(name.split()) > 5
        or name in RESERVED_SKILL_NAMES
        or SKILL_STEP_OK.match(name)
    ):
        return (
            "Please pick a name that is not a command, "
            "like study mode."
        ) if explicit else None

    skills = skills_load()
    existing = find_skill(name, skills)

    if existing:
        existing["steps"] = steps
        verb = "updated"
    else:
        if len(skills) >= MAX_SKILLS:
            return "I have too many skills. Forget one first."

        skills.append({"name": name, "steps": steps})
        verb = "learned"

    skills_save(skills)

    return (
        f"Okay, I {verb} {name}: "
        + ", then ".join(steps)
        + f". Say {name} to run it."
    )


def skill_command(t, focus_cb=None):
    # Returns a spoken reply, or None if t is not about skills.

    # LIST
    if re.match(
        r"^(?:(?:what|which) skills (?:do you have|have you learned|"
        r"did you learn)|(?:list|show|read) (?:my |all |the )?skills|"
        r"what are my skills|my skills)$",
        t,
    ):
        skills = skills_load()

        if not skills:
            return (
                "I haven't learned any skills yet. Say learn, then a "
                "name, then the steps. For example: learn study mode, "
                "search lo-fi music on YouTube, click first video."
            )

        return "My skills are: " + ", ".join(s["name"] for s in skills) + "."

    # FORGET
    m = re.match(
        r"^(?:forget|delete|remove) (?:the |my )?(?:skill|routine) "
        r"(?:called |named )?(.+)$",
        t,
    )

    if m:
        skills = skills_load()
        skill = find_skill(m.group(1), skills)

        if not skill:
            return "I don't have a skill with that name."

        skills.remove(skill)
        skills_save(skills)

        return f"Okay, I forgot {skill['name']}."

    # ADD STEPS TO AN EXISTING SKILL
    m = re.match(
        r"^add (?:a )?(?:steps? )?to (?:the |my |skill )?(.+?)\s*[:,]\s*(.+)$",
        t,
    )

    if m:
        skills = skills_load()
        skill = find_skill(m.group(1), skills)

        if not skill:
            return "I don't have a skill with that name."

        new_steps, error = check_skill_steps(split_skill_steps(m.group(2)))

        if error:
            return error

        if len(skill["steps"]) + len(new_steps) > MAX_SKILL_STEPS:
            return (
                f"A skill can have at most {MAX_SKILL_STEPS} steps."
            )

        skill["steps"].extend(new_steps)
        skills_save(skills)

        return f"Okay, I added that to {skill['name']}."

    # TEACH: "when I say X, do Y"
    m = re.match(
        r"^when i say (.+?),? (?:then )?(?:please )?(?:do|run) (.+)$",
        t,
    )

    if m:
        return _teach_skill(m.group(1), m.group(2), True)

    # TEACH: "learn X: steps" / "create a skill X: steps"
    m = re.match(
        r"^(?:(learn|teach)|(?:create|add|save|make) (?:a |new )*"
        r"(?:skill|routine))\s+(?:a |new )*(?:skill |routine )?"
        r"(?:called |named )?(.+?)\s*(?::|,|\bthat does\b|\bwhich does\b)\s*(.+)$",
        t,
    )

    if m:
        # Plain "learn ..." is only a skill if the steps are valid.
        return _teach_skill(m.group(2), m.group(3), m.group(1) is None)

    # RUN
    skills = skills_load()

    if skills:
        m = re.match(
            r"^(?:(?:run|start|do|activate|launch|play|begin)\s+)?"
            r"(?:my\s+|the\s+)?(?:skill\s+|routine\s+)?(.+?)"
            r"(?:\s+(?:skill|routine))?$",
            t,
        )

        if m:
            skill = find_skill(m.group(1), skills)

            if skill:
                return run_skill(skill, focus_cb)

    return None


def online_command(t):
    # Returns a spoken reply, or None if t is not an online command.

    # WHICH BRAIN
    if re.match(
        r"^(?:are you|am i|are we) (?:online|offline|connected)|"
        r"^(?:which|what) (?:model|brain|ai) (?:are you using|do you use|is this)$",
        t,
    ):
        if cloud_ready():
            return (
                "I'm online. I'm using "
                + online_brain_name()
                + ". If the internet drops, I switch to my local model."
            )

        if internet_ok():
            return (
                "I'm online, but the cloud model is not set up, "
                "so I'm using my local model."
            )

        return "I'm offline. I'm running fully on my local model."

    # AIR QUALITY
    m = re.match(
        r"^(?:what(?:'s|s| is)?|how(?:'s|s| is)?|tell me|check)?\s*"
        r"(?:the\s+)?(?:air quality|aqi|pollution)\s*(?:like\s+)?"
        r"(?:(?:in|of|at|for|near)\s+(.+?))?"
        r"(?:\s+(?:today|now|right now))?$",
        t,
    )

    if m:
        city = (m.group(1) or "").strip() or DEFAULT_CITY

        if not city:
            return "Which city? Say air quality in, then the city name."

        return get_air_quality(city)

    # CAN I GO OUT?
    m = re.match(
        r"^(?:(?:can|should|may) i (?:go|step|walk) (?:out|outside)|"
        r"is it (?:ok|okay|safe|fine|good|alright)"
        r"(?: for me)? to (?:go|step) (?:out|outside)|"
        r"is it (?:ok|okay|safe|fine|good|alright) outside)"
        r"(?:\s+(?:in|at)\s+(.+?))?"
        r"(?:\s+(?:today|now|right now))?$",
        t,
    )

    if m:
        city = (m.group(1) or "").strip() or DEFAULT_CITY

        if not city:
            return "Which city? Say can I go outside in, then the city name."

        return go_out_advice(city)

    # WEATHER
    m = re.match(
        r"^(?:what(?:'s|s| is)?|how(?:'s|s| is)?|tell me|give me|"
        r"show me|check)?\s*(?:the\s+)?(?:current\s+)?"
        r"(?:weather|temperature)\s*(?:like\s+)?"
        r"(?:(?:in|of|at|for|near)\s+(.+?))?"
        r"(?:\s+(?:today|now|right now))?$",
        t,
    )

    if m:
        city = (m.group(1) or "").strip() or DEFAULT_CITY

        if not city:
            return (
                "Which city? Say weather in, then the city name."
            )

        return get_weather(city)

    # NEWS
    if re.match(
        r"^(?:(?:what(?:'s|s| is)?|tell me|read|give me|show me)\s+)?"
        r"(?:the\s+)?(?:latest\s+|top\s+|today's\s+|todays\s+)?"
        r"(?:news|headlines)(?:\s+(?:today|now))?$",
        t,
    ):
        return get_news()

    # WIKIPEDIA
    m = re.match(
        r"^(?:search\s+)?wikipedia\s+(?:for\s+|about\s+)?(.+)$|"
        r"^(?:search\s+|look up\s+|tell me about\s+)?(.+?)\s+on wikipedia$",
        t,
    )

    if m:
        topic = (m.group(1) or m.group(2) or "").strip()

        if topic:
            return get_wiki(topic)

    return None


def extra_command(text, focus_cb=None):

    orig = text.strip()
    o = orig.strip(" .!?,")
    t = re.sub(r"\s+", " ", o.lower()).strip()

    if not t:
        return None

    # ----------------------------------------------------
    # DICTATION
    # ----------------------------------------------------

    if re.match(
        r"^(?:stop|end|exit|turn off)\s+"
        r"(?:typing|dictation|dictating)(?:\s+mode)?$",
        t,
    ):
        if DICTATION["on"]:
            DICTATION["on"] = False
            return ("say", "Dictation off.")
        return ("say", "Dictation is not on.")

    if DICTATION["on"]:

        if t in DICTATION_JUNK:
            return ("say", "")

        try:
            if t in ("new line", "next line", "enter"):
                pyautogui.press("enter")
                return ("say", "Typed: new line")

            pyautogui.write(orig + " ", interval=0.01)
        except Exception as error:
            print("DICTATION ERROR:", error)
            return ("say", "I couldn't type that.")

        return ("say", "Typed: " + orig)

    if re.match(
        r"^(?:start|begin|turn on)\s+"
        r"(?:typing|dictation|dictating)(?:\s+mode)?$"
        r"|^dictation(?:\s+mode)?(?:\s+on)?$",
        t,
    ):
        DICTATION["on"] = True

        if focus_cb:
            focus_cb()

        return (
            "say",
            "Dictation on. Click where you want to type "
            "and start speaking. Say stop typing to finish.",
        )

    # ----------------------------------------------------
    # ONLINE: weather, news, wikipedia, which brain
    # ----------------------------------------------------

    online_reply = online_command(t)

    if online_reply is not None:
        return ("say", online_reply)

    # ----------------------------------------------------
    # TEACHABLE SKILLS
    # ----------------------------------------------------

    skill_reply = skill_command(t, focus_cb)

    if skill_reply is not None:
        return ("say", skill_reply)

    # ----------------------------------------------------
    # ROCK PAPER SCISSORS
    # ----------------------------------------------------

    reply = rps_command(t)

    if reply is not None:
        return ("say", reply)

    # ----------------------------------------------------
    # MEMORY
    # ----------------------------------------------------

    m = re.match(r"^remember\s+(?:that\s+)?(.+)$", o, re.I)

    if m:
        fact = m.group(1).strip()

        if is_blocked_request(fact) or re.search(r"\b\d{9,}\b", fact):
            return (
                "say",
                "I won't store that, it looks private. "
                "Memory is a plain file.",
            )

        mem_add(fact)
        return ("say", "Okay, I'll remember that.")

    if re.match(
        r"^(?:what do you remember|what do you know about me|"
        r"what did i tell you|read (?:my )?memory)$",
        t,
    ):
        facts = mem_load()

        if not facts:
            return ("say", "I don't remember anything yet.")

        return ("say", "I remember: " + "; ".join(facts[-10:]) + ".")

    if t in ("forget everything", "clear memory", "clear my memory"):
        _json_save(MEM_FILE, [])
        return ("say", "Memory cleared.")

    if t in ("forget that", "forget the last thing", "forget last"):
        facts = mem_load()

        if not facts:
            return ("say", "There's nothing to forget.")

        facts.pop()
        _json_save(MEM_FILE, facts)
        return ("say", "Forgot the last thing.")

    # ----------------------------------------------------
    # TASKS / NOTES
    # ----------------------------------------------------

    m = re.match(
        r"^(?:add\s+(?:a\s+)?(?:task|todo|to-do|note)|"
        r"take\s+a\s+note|note\s+down|note\s+that)\s*"
        r"(?:to\s+|that\s+)?(.+)$",
        o,
        re.I,
    )

    if m:
        tasks = _json_load(TASK_FILE)
        tasks.append(m.group(1).strip())
        _json_save(TASK_FILE, tasks)
        return ("say", f"Added. You have {len(tasks)} {_item_word(len(tasks))}.")

    if re.match(
        r"^(?:read|show|what are|what's on)?\s*"
        r"(?:my\s+)?(?:tasks|to-?do list|todo list|notes)$",
        t,
    ):
        tasks = _json_load(TASK_FILE)

        if not tasks:
            return ("say", "Your list is empty.")

        parts = [f"{i + 1}, {x}" for i, x in enumerate(tasks[:8])]
        return (
            "say",
            f"You have {len(tasks)} {_item_word(len(tasks))}. "
            + ". ".join(parts),
        )

    m = re.match(
        r"^(?:complete|finish|done with|remove|delete)\s+"
        r"(?:task|item|note)\s+(\w+)$",
        t,
    )

    if m:
        tasks = _json_load(TASK_FILE)
        n = to_number(m.group(1))

        if n is None or not (1 <= int(n) <= len(tasks)):
            return ("say", "I couldn't find that number.")

        removed = tasks.pop(int(n) - 1)
        _json_save(TASK_FILE, tasks)
        return ("say", f"Removed {removed}.")

    if t in ("clear tasks", "clear my tasks", "clear my list", "clear notes"):
        _json_save(TASK_FILE, [])
        return ("say", "List cleared.")

    # ----------------------------------------------------
    # REMINDERS / TIMERS
    # ----------------------------------------------------

    m = re.match(
        r"^(?:remind me|set (?:a )?reminder)\s+(?:in|after)\s+"
        r"(.+?)\s+" + UNIT_RE + r"\b\s*(?:to|that|about)?\s*(.*)$",
        t,
    )

    if m:
        message = m.group(3).strip()
        spoken = (
            "Reminder: " + message if message else "Time is up!"
        )
        result = set_reminder(m.group(1), m.group(2), spoken)

        if result is None:
            return ("say", "I didn't catch the time.")

        if result.startswith("That's too long"):
            return ("say", result)

        tail = f" to {message}" if message else ""
        return ("say", f"Okay, I'll remind you in {result}{tail}.")

    m = re.match(
        r"^(?:set (?:a )?|start (?:a )?)?(?:timer|alarm)"
        r"(?:\s+for|\s+of)?\s+(.+?)\s+" + UNIT_RE + r"$",
        t,
    )

    if m:
        result = set_reminder(m.group(1), m.group(2), "Time is up!")

        if result is None:
            return ("say", "I didn't catch the time.")

        if result.startswith("That's too long"):
            return ("say", result)

        return ("say", f"Timer set for {result}.")

    # ----------------------------------------------------
    # VOLUME
    # ----------------------------------------------------

    if re.match(
        r"^(?:(?:turn up|increase|raise)\s+(?:the\s+)?(?:volume|sound)|"
        r"(?:volume|sound)\s+up|louder)$",
        t,
    ):
        pyautogui.press("volumeup", presses=5)
        return ("say", "Volume up.")

    if re.match(
        r"^(?:(?:turn down|decrease|lower|reduce)\s+(?:the\s+)?"
        r"(?:volume|sound)|(?:volume|sound)\s+down|quieter)$",
        t,
    ):
        pyautogui.press("volumedown", presses=5)
        return ("say", "Volume down.")

    if re.match(
        r"^(?:(?:full|max|maximum)\s+volume|volume\s+(?:full|max))$",
        t,
    ):
        pyautogui.press("volumeup", presses=50)
        return ("say", "Volume at maximum.")

    if re.match(
        r"^(?:(?:mute|unmute)(?:\s+(?:the\s+)?"
        r"(?:volume|sound|audio|pc|computer))?|(?:volume|sound)\s+"
        r"(?:mute|off))$",
        t,
    ):
        pyautogui.press("volumemute")
        return ("say", "Done.")

    # ----------------------------------------------------
    # CLIPBOARD HELPER
    # ----------------------------------------------------

    if re.match(
        r"^(?:read|what(?:'s| is| did i)?)\s+"
        r"(?:(?:in )?(?:my )?clipboard|what i copied|i copy|copied)$",
        t,
    ):
        clip = get_clipboard()

        if not clip:
            return ("say", "Your clipboard is empty.")

        return ("say", "You copied: " + clip[:300])

    m = re.match(
        r"^(explain|summari[sz]e)\s+"
        r"(?:what i copied|(?:my )?clipboard|copied text)$",
        t,
    )

    if m:
        clip = get_clipboard()

        if not clip:
            return ("say", "Your clipboard is empty. Copy some text first.")

        verb = "Explain" if m.group(1) == "explain" else "Summarize"
        return (
            "ask_ai",
            f"{verb} this in 2 or 3 short sentences:\n\n{clip[:1500]}",
            180,
        )

    m = re.match(
        r"^translate\s+(?:what i copied|(?:my )?clipboard|copied text)"
        r"\s+(?:to|into)\s+(\w+)$",
        t,
    )

    if m:
        clip = get_clipboard()

        if not clip:
            return ("say", "Your clipboard is empty. Copy some text first.")

        return (
            "ask_ai",
            f"Translate this to {m.group(1)}. "
            f"Reply with only the translation:\n\n{clip[:1500]}",
            200,
        )

    # ----------------------------------------------------
    # HELP
    # ----------------------------------------------------

    if re.match(
        r"^(?:what can (?:i|you) (?:say|do)|help|help me|"
        r"show (?:the )?commands|list (?:the )?commands)$",
        t,
    ):
        return ("say", HELP_TEXT)

    # ----------------------------------------------------
    # SCREEN VISION  (needs a vision model in LM Studio)
    # ----------------------------------------------------

    if re.match(
        r"^(?:what(?:'s|s| is| s)?\s+on\s+(?:my\s+|the\s+)?screen|"
        r"(?:look at|read|describe|explain|summari[sz]e)\s+"
        r"(?:my\s+|the\s+|this\s+)?(?:screen|page)|"
        r"(?:explain|what(?:'s|s| is| s)?)\s+(?:this|the)\s+error)$",
        t,
    ):
        if "error" in t:
            return (
                "ask_screen",
                "There may be an error message on this screen. Explain in "
                "simple words what it means, then give the fix in at most "
                "3 short steps.",
                250,
            )

        if t.startswith("read"):
            return (
                "ask_screen",
                "Read the main text on this screen out loud in a natural "
                "way. Skip menus, ads and buttons. Keep it under 120 words.",
                300,
            )

        return (
            "ask_screen",
            "Look at this screenshot. In at most 3 short sentences, tell "
            "me what is on the screen and what I could do next.",
            200,
        )

    # ----------------------------------------------------
    # SOLVE COPIED QUESTIONS -> NOTEPAD
    # ----------------------------------------------------

    if re.match(
        r"^(?:solve|answer)\s+(?:what i copied|(?:my\s+|the\s+)?clipboard|"
        r"(?:the\s+|these\s+)?(?:copied\s+)?(?:questions?|text)|it|this)"
        r"(?:\s+(?:in|on|to|into)\s+notepad)?$",
        t,
    ):
        clip = get_clipboard()

        if not clip.strip():
            return (
                "say",
                "Your clipboard is empty. Copy the questions first.",
            )

        kind = "solve_notepad" if "notepad" in t else "solve_page"
        return (kind, clip[:3000], 1500)

    # ----------------------------------------------------
    # STUDY HELPER
    # ----------------------------------------------------

    m = re.match(r"^quiz me (?:on|about)\s+(.+)$", t)

    if m:
        return (
            "ask_ai",
            f"Ask me ONE short quiz question about {m.group(1)}. "
            "Do not give the answer yet.",
            100,
        )

    return None


# =========================================================
# LOAD WHISPER
# =========================================================

print("Loading Whisper model...")

try:

    stt = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
    )

    print(
        "Whisper base model loaded."
    )

except Exception as error:

    print(
        "Base model failed:",
        error,
    )

    print(
        "Trying tiny model..."
    )

    stt = WhisperModel(
        "tiny",
        device="cpu",
        compute_type="int8",
    )


# =========================================================
# WORKER
# =========================================================

class Worker(QThread):

    status = Signal(str)
    heard = Signal(str)
    reply = Signal(str)

    minimize = Signal()
    restore = Signal()

    def __init__(
        self,
        client,
        model_id,
        history,
        text=None,
    ):

        super().__init__()

        self.client = client
        self.model_id = model_id
        self.history = history
        self.text = text

        # Set by extra commands that want the AI to answer
        self.ai_text = None
        self.ai_tokens = (
            220
            if persona_load()["mode"] == "friend"
            else 80
        )

        # Screen vision / solve-to-Notepad state
        self.screen_b64 = None
        self.solve_mode = False
        self.solve_mode_kind = None

    # -----------------------------------------------------

    def speak(self, text):

        self.status.emit(
            "SPEAKING"
        )

        try:

            try:

                import pythoncom

                pythoncom.CoInitialize()

            except Exception:
                pass

            engine = pyttsx3.init()

            engine.setProperty(
                "rate",
                190,
            )

            engine.setProperty(
                "volume",
                1.0,
            )

            speech = clean_for_speech(
                text
            )

            if speech:

                engine.say(
                    speech
                )

                engine.runAndWait()

        except Exception as error:

            print(
                "VOICE OUTPUT ERROR:",
                error,
            )

    # -----------------------------------------------------

    def _focus(self):

        # Hide SINK so typing / actions go to the other window.
        self.minimize.emit()

        time.sleep(0.7)

    # -----------------------------------------------------

    def handle_command(self, text):

        text = re.sub(
            r"^(?:hey |hi |ok |okay )?"
            r"(?:sink|sync|think|zinc|zink|sing|sank)"
            r"\b[,.! ]*",
            "",
            text,
            flags=re.I,
        ).strip()

        t = text.lower().strip(
            " .!?,"
        )

        if not t:
            return None

        # ---------------------------------------------
        # MAM / SIR  +  FRIEND / ASSISTANT MODE
        # ---------------------------------------------

        persona_reply = persona_command(t)

        if persona_reply is not None:

            return persona_reply

        # ---------------------------------------------
        # CHIBI PERSONALITY
        # ---------------------------------------------

        if re.search(
            LOVE_PATTERN,
            t,
        ):

            return "Awww, love you too. ♥"

        if re.search(
            CUTE_PATTERN,
            t,
        ):

            return "Stop it, you're making me blush!"

        if re.search(
            r"\b(i am angry|i'm angry)\b",
            t,
        ):

            return "Not my problem."

        # SAFETY
        # ---------------------------------------------

        if (
            is_blocked_request(text)
            and re.match(
                r"^(open|type|write|delete|run|click|press|go to)\b",
                t,
            )
        ):

            return (
                "I won't access protected areas."
            )

        # ---------------------------------------------
        # RESTORE
        # ---------------------------------------------

        if t in (
            "show sink",
            "come back",
            "show yourself",
        ):

            self.restore.emit()

            return "I'm here."

        # ---------------------------------------------
        # EXTRA FEATURES
        # game, memory, tasks, reminders, volume,
        # dictation, clipboard helper, quiz
        # ---------------------------------------------

        extra = extra_command(
            strip_fillers(text),
            self._focus,
        )

        if extra is not None:

            if extra[0] == "ask_ai":

                self.ai_text = extra[1]
                self.ai_tokens = extra[2]

                return None

            if extra[0] == "ask_screen":

                # Hide SINK so it is not in the screenshot.
                self._focus()

                try:
                    self.screen_b64 = screen_to_b64()
                except Exception as error:
                    print("SCREENSHOT ERROR:", error)
                    return "I couldn't take a screenshot."

                self.ai_text = extra[1]
                self.ai_tokens = extra[2]

                return None

            if extra[0] in ("solve_notepad", "solve_page"):

                self.ai_text = extra[1]
                self.ai_tokens = extra[2]
                self.solve_mode = (
                    "notepad"
                    if extra[0] == "solve_notepad"
                    else "page"
                )

                # Hide SINK so the chibi (with SOLVING...) is what you see.
                self._focus()

                return None

            return extra[1]

        # ---------------------------------------------
        # MINIMIZE
        # ---------------------------------------------

        if needs_focus(text):

            self.minimize.emit()

            time.sleep(0.7)

        reply = run_command(text)

        # SINK did not understand it -> let Nemotron plan the steps.
        if reply is None:
            reply = self.agent_command(text)

        return reply

    # -----------------------------------------------------

    def agent_command(self, text):

        # Online only. Offline, SINK behaves exactly as before.
        if not cloud_ready():
            return None

        if not ACTION_HINT.search(text):
            return None

        steps = plan_steps(text)

        print("AGENT PLAN:", steps)

        # Safety: every step must be a known, safe command.
        for step in steps:

            if is_blocked_request(step) or DANGEROUS_KEYS.search(step):

                return "I won't do that, it touches protected areas."

        steps = [s for s in steps if ALLOWED_STEP.match(s)]

        if not steps:
            return None

        if needs_focus(" and ".join(steps)):
            self._focus()

        done = []

        for i, step in enumerate(steps):

            reply = run_step(step)

            print("AGENT STEP:", step, "->", reply)

            if reply is None:
                continue

            done.append(step)

            if reply.startswith("Opening") and i < len(steps) - 1:
                time.sleep(2.5)

        if not done:
            return None

        return "Okay. I did: " + ", then ".join(done) + "."

    # -----------------------------------------------------

    def write_solution_page(self, answer):

        from pathlib import Path

        path = os.path.join(BASE_DIR, "sink_solution.html")

        with open(path, "w", encoding="utf-8") as f:
            f.write(build_solution_html(answer))

        self.minimize.emit()

        time.sleep(0.5)

        webbrowser.open(Path(path).as_uri())

    # -----------------------------------------------------

    def write_to_notepad(self, answer):

        # Hide SINK, open Notepad, paste the solution + a check note.
        self.minimize.emit()

        subprocess.Popen(
            'start "" notepad',
            shell=True,
        )

        time.sleep(2.5)

        full = answer + SOLVE_NOTE

        if set_clipboard(full):
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(full, interval=0.005)

    # -----------------------------------------------------

    def _complete(self, messages, temperature):

        # 1) Online brains (Groq / Gemini), only when online.
        #    Screenshots always stay local (they need a vision model).
        if not self.screen_b64 and cloud_ready():

            # Routing: heavy (Gemini first) for solve,
            # fast (Groq first) for everything else.
            result = cloud_chat(
                messages,
                self.ai_tokens,
                temperature,
                heavy=bool(self.solve_mode),
            )

            if result is not None:
                return result

            print("ONLINE: no brain answered, using local model.")
            _NET["t"] = 0.0

        # 2) Local brain: LM Studio (works with no internet).
        if self.model_id is None:

            try:

                self.model_id = (
                    self.client
                    .with_options(
                        timeout=3,
                        max_retries=0,
                    )
                    .models
                    .list()
                    .data[0]
                    .id
                )

            except Exception:

                raise RuntimeError(
                    "No AI is available. The internet or cloud model "
                    "is off and the LM Studio server is not running."
                )

        print("AI BACKEND: LM Studio (local)")

        return self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
            max_tokens=self.ai_tokens,
            temperature=temperature,
        )

    # -----------------------------------------------------

    def run(self):

        try:

            text = self.text

            # -----------------------------------------
            # VOICE INPUT
            # -----------------------------------------

            if text is None:

                self.status.emit(
                    "LISTENING"
                )

                rec = sd.rec(
                    int(5 * 16000),
                    samplerate=16000,
                    channels=1,
                    dtype="float32",
                )

                sd.wait()

                segments, _ = stt.transcribe(
                    rec.flatten(),
                    language="en",
                    initial_prompt=INITIAL_PROMPT,
                    beam_size=1,
                )

                text = " ".join(
                    s.text
                    for s in segments
                ).strip()

                if not text:

                    self.reply.emit(
                        "I didn't catch that."
                    )

                    return

            # -----------------------------------------

            self.heard.emit(
                text
            )

            # -----------------------------------------
            # PC COMMANDS FIRST
            # -----------------------------------------

            command_reply = (
                self.handle_command(
                    text
                )
            )

            if command_reply is not None:

                self.reply.emit(
                    command_reply
                )

                if (
                    command_reply
                    and not command_reply.startswith("Typed:")
                ):

                    self.speak(
                        command_reply
                    )

                return

            # -----------------------------------------
            # AI
            # -----------------------------------------

            self.status.emit(
                "SOLVING"
                if self.solve_mode
                else "THINKING"
            )

            if self.ai_text:
                text = self.ai_text

            # Local memory -> system prompt
            try:

                base = self.history[0]["content"].split(
                    "\n\nKnown facts about the user:"
                )[0]

                # Mam / Sir + assistant / friend mode
                base = base.split("\n\nSINK persona:")[0]
                base += "\n\n" + persona_text()

                facts = mem_load()

                if facts:
                    base += (
                        "\n\nKnown facts about the user: "
                        + "; ".join(facts[-40:])
                    )

                self.history[0]["content"] = base

            except Exception as error:
                print("MEMORY PROMPT ERROR:", error)

            # Screen / solve requests are one-off: keep them
            # (and the screenshot) out of the chat history.
            if not (self.solve_mode or self.screen_b64):

                self.history.append({
                    "role": "user",
                    "content": text,
                })

            # (model lookup now happens inside _complete)

            # -----------------------------------------
            # AI REQUEST
            # -----------------------------------------

            messages = self.history
            temperature = 0.7

            if self.solve_mode:

                temperature = 0.2

                messages = [
                    {
                        "role": "system",
                        "content": (
                            SOLVE_PROMPT_PAGE
                            if self.solve_mode == "page"
                            else SOLVE_PROMPT_TEXT
                        ),
                    },
                    {
                        "role": "user",
                        "content": text,
                    },
                ]

            elif self.screen_b64:

                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are SINK, a voice assistant for a "
                            "person who cannot use their hands. You "
                            "are given a screenshot of their screen. "
                            "Answer in simple words and short "
                            "sentences. Plain text only, no markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        "data:image/jpeg;base64,"
                                        + self.screen_b64
                                    ),
                                },
                            },
                        ],
                    },
                ]

            result = self._complete(
                messages,
                temperature,
            )

            answer = (
                result
                .choices[0]
                .message
                .content
            )

            answer = re.sub(
                r"<think>.*?</think>",
                "",
                answer,
                flags=re.S | re.I,
            ).strip()

            if self.solve_mode:

                self.solve_mode_kind = self.solve_mode
                self.solve_mode = False

                if not answer:

                    self.reply.emit(
                        "The model gave an empty answer. Try again."
                    )

                    return

                if "NO_QUESTIONS" in answer.upper():

                    nq = (
                        "I couldn't find any questions in what you "
                        "copied. Copy the questions and try again."
                    )

                    self.reply.emit(nq)
                    self.speak(nq)

                    return

                if self.solve_mode_kind == "page":

                    self.write_solution_page(answer)

                    done = (
                        "Your solution is open in the browser. "
                        "The final answers are at the top. "
                        "Please check it once, I can make mistakes."
                    )

                else:

                    self.write_to_notepad(
                        build_notepad_text(answer)
                    )

                    done = (
                        "Solution written in Notepad. "
                        "The final answers are at the top. "
                        "Please check it once, I can make mistakes."
                    )

                self.reply.emit(done)
                self.speak(done)

                return

            if not self.screen_b64:

                self.history.append({
                    "role": "assistant",
                    "content": answer,
                })

            self.reply.emit(
                answer
            )

            self.speak(
                answer
            )

        except Exception as error:

            print(
                "WORKER ERROR:",
                error,
            )

            self.reply.emit(
                "SINK ERROR: "
                + str(error)
            )


# =========================================================
# ALWAYS-ON LISTENER
# =========================================================

class ListenLoop(QThread):

    command = Signal(str)
    error = Signal(str)

    # Emitted once, 3 seconds after the user stops talking.
    quiet = Signal()

    def __init__(self):

        super().__init__()

        self.running = True
        self.paused = False

    # -----------------------------------------------------

    def transcribe(self, audio):

        # Make quiet speech louder before recognition
        peak = (
            float(np.max(np.abs(audio)))
            if len(audio)
            else 0.0
        )

        if peak > 0.01:
            audio = (
                audio * (0.9 / peak)
            ).astype(np.float32)

        segments, _ = stt.transcribe(
            audio,
            language="en",
            initial_prompt=INITIAL_PROMPT,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )

        text = " ".join(
            s.text
            for s in segments
        ).strip()

        if not text:
            return

        print(
            "HEARD:",
            text,
        )

        m = re.match(
            WAKE_PATTERN,
            text,
            re.I,
        )

        if not m:

            # "go to sleep SINK" (wake word at the end)
            if re.match(
                r"^(?:go to sleep|sleep|good night)[,.! ]*"
                r"(?:sink|sync|think|zinc|zink|sing|sank)\W*$",
                text,
                re.I,
            ):

                self.command.emit(
                    "go to sleep"
                )

                return

            # While a game or dictation is running,
            # no wake word is needed.
            if session_active():

                print(
                    "SESSION INPUT:",
                    text,
                )

                self.command.emit(
                    text
                )

            return

        cmd = m.group(1).strip()

        # ---------------------------------------------
        # ONLY SINK
        # ---------------------------------------------

        if not cmd:

            print(
                "WAKE WORD DETECTED"
            )

            self.command.emit(
                "__WAKE_ONLY__"
            )

            return

        # ---------------------------------------------
        # SINK + COMMAND
        # ---------------------------------------------

        print(
            "SINK COMMAND:",
            cmd,
        )

        self.command.emit(
            cmd
        )

    # -----------------------------------------------------

    def run(self):

        try:

            sr = 16000

            block = int(
                sr * 0.1
            )

            with sd.InputStream(
                samplerate=sr,
                channels=1,
                dtype="float32",
                blocksize=block,
            ) as stream:

                noise = []

                for _ in range(10):

                    data, _ = (
                        stream.read(
                            block
                        )
                    )

                    noise.append(
                        float(
                            np.sqrt(
                                np.mean(
                                    data ** 2
                                )
                            )
                        )
                    )

                threshold = max(
                    0.008,
                    float(
                        np.mean(noise)
                    ) * 2.5,
                )

                print(
                    "Microphone threshold:",
                    threshold,
                )

                chunks = []
                silence = 0
                talking = False
                prev = None

                last_speech_end = 0.0
                armed = False

                while self.running:

                    data, _ = (
                        stream.read(
                            block
                        )
                    )

                    if self.paused:

                        chunks = []
                        silence = 0
                        talking = False
                        prev = None
                        armed = False

                        continue

                    level = float(
                        np.sqrt(
                            np.mean(
                                data ** 2
                            )
                        )
                    )

                    if level > threshold:

                        if (
                            not talking
                            and prev is not None
                        ):

                            chunks.append(
                                prev
                            )

                        talking = True
                        silence = 0

                        chunks.append(
                            data.copy()
                        )

                    elif talking:

                        silence += 1

                        chunks.append(
                            data.copy()
                        )

                    done = (
                        talking
                        and (
                            silence >= 8
                            or len(chunks) > 150
                        )
                    )

                    if done:

                        audio = (
                            np.concatenate(
                                chunks
                            ).flatten()
                        )

                        chunks = []
                        silence = 0
                        talking = False

                        if (
                            len(audio)
                            > sr * 0.5
                        ):

                            self.transcribe(
                                audio
                            )

                        last_speech_end = time.monotonic()
                        armed = True

                    # 3 seconds after the user stopped talking
                    if (
                        armed
                        and not talking
                        and (
                            time.monotonic()
                            - last_speech_end
                            >= 3.0
                        )
                    ):

                        armed = False

                        self.quiet.emit()

                    prev = data.copy()

        except Exception as error:

            self.error.emit(
                str(error)
            )


# =========================================================
# MAIN WINDOW
# =========================================================

class SinkWindow(QWidget):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "SINK AI"
        )

        self.worker = None
        self.listener = None
        self.chibi = None

        self.auto_listen = False
        self.last_heard = ""

        # ---------------------------------------------
        # CHIBI TIMERS
        # ---------------------------------------------

        self.heart_timer = QTimer(
            self
        )

        self.heart_timer.setSingleShot(
            True
        )

        self.heart_timer.timeout.connect(
            self.show_love
        )

        self.blush_timer = QTimer(
            self
        )

        self.blush_timer.setSingleShot(
            True
        )

        self.blush_timer.timeout.connect(
            self.clear_blush
        )

        # 3 seconds of silence -> heart eyes
        self.quiet_timer = QTimer(
            self
        )

        self.quiet_timer.setSingleShot(
            True
        )

        self.quiet_timer.timeout.connect(
            self.on_quiet
        )

        # Auto-sleep: no command for AUTO_SLEEP_MIN minutes
        # -> stop the mic listener + hide chibi (saves battery).
        self.sleep_timer = QTimer(
            self
        )

        self.sleep_timer.setSingleShot(
            True
        )

        self.sleep_timer.setInterval(
            AUTO_SLEEP_MIN * 60 * 1000
        )

        self.sleep_timer.timeout.connect(
            self.auto_sleep
        )

        # ---------------------------------------------
        # AI HISTORY
        # ---------------------------------------------

        self.history = [

            {
                "role": "system",
                "content": (
                    "You are SINK, a local desktop AI companion. "
                    "Be casual, friendly and natural. "
                    "Keep replies VERY SHORT. "
                    "Usually answer in one sentence. "
                    "Use at most two short sentences when necessary. "
                    "Never give long explanations unless the user "
                    "specifically asks. "
                    "Do not repeat the user's question. "
                    "Do not sound robotic or overly formal. "
                    "PC actions are handled separately."
                ),
            }

        ]

        self.resize(
            900,
            700,
        )

        # ---------------------------------------------
        # LM STUDIO
        # ---------------------------------------------

        self.client = OpenAI(

            base_url=(
                "http://127.0.0.1:1234/v1"
            ),

            api_key="lm-studio",

            max_retries=0,
        )

        self.model_id = None

        # ---------------------------------------------
        # STYLE
        # ---------------------------------------------

        self.setStyleSheet(
            """
            QWidget {
                background-color: #070604;
                color: #FFF3D1;
                font-family: Segoe UI;
            }

            QLineEdit {
                background-color: #14110A;
                border: 1px solid #8C6C1C;
                border-radius: 10px;
                padding: 12px;
                color: white;
                font-size: 14px;
            }

            QLineEdit:focus {
                border: 1px solid #FFD76A;
            }

            QPushButton {
                background-color: #9A7410;
                border: none;
                border-radius: 10px;
                padding: 12px 20px;
                color: white;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #C29516;
            }

            QPushButton:pressed {
                background-color: #7A5C0A;
            }

            QCheckBox {
                color: #FFF3D1;
            }
            """
        )

        # ---------------------------------------------
        # MAIN LAYOUT
        # ---------------------------------------------

        layout = QVBoxLayout()

        layout.setContentsMargins(
            30,
            20,
            30,
            25,
        )

        # ---------------------------------------------
        # TITLE
        # ---------------------------------------------

        title = QLabel(
            "S I N K"
        )

        title.setAlignment(
            Qt.AlignCenter
        )

        title.setStyleSheet(
            """
            QLabel {
                color: #FFD76A;
                font-size: 28px;
                font-weight: bold;
                letter-spacing: 8px;
            }
            """
        )

        layout.addWidget(
            title
        )

        # ---------------------------------------------
        # STATUS
        # ---------------------------------------------

        self.status = QLabel(
            "● SINK ONLINE"
        )

        self.status.setAlignment(
            Qt.AlignCenter
        )

        self.status.setStyleSheet(
            """
            QLabel {
                color: #5CFF9A;
                font-size: 13px;
            }
            """
        )

        layout.addWidget(
            self.status
        )

        # ---------------------------------------------
        # CORE
        # ---------------------------------------------

        self.core = SinkCore()

        layout.addWidget(
            self.core,
            1,
        )

        # ---------------------------------------------
        # RESPONSE
        # ---------------------------------------------

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

        self.response.setStyleSheet(
            """
            QLabel {
                background-color: #100D07;
                border: 1px solid #4A3A12;
                border-radius: 12px;
                padding: 15px;
                color: #FFF1C7;
                font-size: 14px;
            }
            """
        )

        layout.addWidget(
            self.response
        )

        # ---------------------------------------------
        # INPUT
        # ---------------------------------------------

        input_layout = QHBoxLayout()

        self.input = QLineEdit()

        self.input.setPlaceholderText(
            "Talk to SINK..."
        )

        self.input.returnPressed.connect(
            self.send_message
        )

        input_layout.addWidget(
            self.input,
            1,
        )

        mic_button = QPushButton(
            "🎤 SPEAK"
        )

        mic_button.clicked.connect(
            self.listen_message
        )

        self.hf_button = QPushButton(
            "🎧 HANDS-FREE"
        )

        self.hf_button.clicked.connect(
            self.toggle_handsfree
        )

        send_button = QPushButton(
            "SEND"
        )

        send_button.clicked.connect(
            self.send_message
        )

        input_layout.addWidget(
            mic_button
        )

        input_layout.addWidget(
            self.hf_button
        )

        input_layout.addWidget(
            send_button
        )

        layout.addLayout(
            input_layout
        )

        # ---------------------------------------------
        # COMMAND CHEAT SHEET
        # ---------------------------------------------

        command_title = QLabel(
            "VOICE COMMANDS"
        )

        command_title.setStyleSheet(
            """
            QLabel {
                color: #FFD76A;
                font-size: 12px;
                font-weight: bold;
                margin-top: 5px;
            }
            """
        )

        layout.addWidget(
            command_title
        )

        self.command_help = QLabel(
            "OPEN:  SINK open Chrome  •SINK open YouTube in Chrome\n"
            "CLICK:  SINK click  \u2022  SINK click Send  \u2022  double click\n"
            "SCREEN:  SINK what's on my screen  \u2022  explain this error  \u2022  "
            "SINK solve what I copied\n"
            "MOUSE:  SINK move mouse right 200  •  "
            "TYPE:  SINK type hello world\n"
            "SHORTCUTS:  SINK copy  •  paste  •  select all  •  "
            "SEARCH:  SINK search Python tutorials"
        )

        self.command_help.setWordWrap(
            True
        )

        self.command_help.setStyleSheet(
            """
            QLabel {
                color: #B8A979;
                background-color: #0D0B07;
                border: 1px solid #30260E;
                border-radius: 8px;
                padding: 8px;
                font-size: 10px;
            }
            """
        )

        layout.addWidget(
            self.command_help
        )

        # ---------------------------------------------
        # SETTINGS
        # ---------------------------------------------

        settings_row = QHBoxLayout()

        self.auto_box = QCheckBox(
            "Keep listening after SINK speaks"
        )

        self.auto_box.stateChanged.connect(
            lambda state: setattr(
                self,
                "auto_listen",
                bool(state),
            )
        )

        chibi_button = QPushButton(
            "🤖 MINIMIZE TO CHIBI"
        )

        chibi_button.clicked.connect(
            self.minimize_to_chibi
        )

        settings_row.addWidget(
            self.auto_box
        )

        settings_row.addStretch()

        settings_row.addWidget(
            chibi_button
        )

        layout.addLayout(
            settings_row
        )

        self.setLayout(
            layout
        )

    # =====================================================
    # SHORTCUT KEY  /  WAKE  /  SLEEP
    # =====================================================

    def is_awake(self):

        return (
            self.isVisible()
            or (
                self.chibi is not None
                and self.chibi.isVisible()
            )
        )

    def wake_sink(self):

        ensure_lm_studio()

        self.create_chibi()

        self.chibi.wake_up()

        if (
            self.listener is None
            or not self.listener.running
        ):

            self.toggle_handsfree()

        self.sleep_timer.start()

    def sleep_sink(self):

        self.sleep_timer.stop()

        if (
            self.listener is not None
            and self.listener.running
        ):

            self.toggle_handsfree()

        if self.chibi is not None:

            self.chibi.hide()

        self.hide()

    def hotkey_toggle(self):

        if self.is_awake():

            self.sleep_sink()

        else:

            self.wake_sink()

    def auto_sleep(self):

        busy = (
            self.worker is not None
            and self.worker.isRunning()
        )

        # Do not sleep while a command runs, a game or
        # dictation is active, or the dashboard is open.
        if busy or self.isVisible() or session_active():

            self.sleep_timer.start()

            return

        self.sleep_sink()

    def show_dashboard(self):

        self.restore_from_chibi()

        self.sleep_timer.start()

    def quit_sink(self):

        if self.listener is not None:

            self.listener.running = False

            self.listener.wait(
                1500
            )

        QApplication.quit()

    # =====================================================
    # CREATE CHIBI
    # =====================================================

    def create_chibi(self):

        if self.chibi is None:

            self.chibi = ChibiRobot()

            self.chibi.clicked.connect(
                self.restore_from_chibi
            )

        screen = (
            QApplication
            .primaryScreen()
            .availableGeometry()
        )

        x = max(10, screen.right() - self.chibi.width() - 35)
        y = max(10, screen.bottom() - self.chibi.height() - 35)

        self.chibi.move(x, y)
        self.chibi.show()
        self.chibi.showNormal()
        self.chibi.raise_()
        self.chibi.update()

    # =====================================================
    # CHIBI
    # =====================================================

    def minimize_to_chibi(self):

        self.create_chibi()

        self.chibi.stop_love()
        self.chibi.stop_wake_animation()

        self.chibi.set_emotion(
            "idle"
        )

        self.chibi.set_status_text(
            ""
        )

        self.chibi.show()

        self.hide()

    # -----------------------------------------------------

    def restore_from_chibi(self):

        if self.chibi:

            self.chibi.hide()

        self.show()

        self.raise_()

        self.activateWindow()

    # -----------------------------------------------------

    def set_chibi_emotion(
        self,
        emotion,
    ):

        if self.chibi:

            self.chibi.set_emotion(
                emotion
            )

    # -----------------------------------------------------

    def set_chibi_status(
        self,
        text,
    ):

        if self.chibi:

            self.chibi.set_status_text(
                text
            )

    # =====================================================
    # LOVE
    # =====================================================

    def show_love(self):

        self.create_chibi()

        self.chibi.show_love()

    # -----------------------------------------------------

    def show_blush(self):

        self.create_chibi()

        self.chibi.show_blush()

        self.blush_timer.start(
            4200
        )

    # -----------------------------------------------------

    def on_quiet(self):

        # 3 seconds after the user stopped talking
        # (or SINK finished replying) -> heart eyes.

        if self.chibi is None:
            return

        if not self.chibi.isVisible():
            return

        if (
            self.worker is not None
            and self.worker.isRunning()
        ):
            return

        if DICTATION["on"]:
            return

        if (
            self.chibi.love_mode
            or self.chibi.blush_mode
            or self.chibi.wake_animation
        ):
            return

        self.chibi.show_love()

    # -----------------------------------------------------

    def clear_blush(self):

        if self.chibi:

            if self.chibi.blush_mode:

                self.chibi.stop_blush()

            elif self.chibi.love_mode:

                self.chibi.stop_love()

            elif self.chibi.emotion in (
                "blush",
                "love",
            ):

                self.chibi.set_emotion(
                    "idle"
                )

                self.chibi.set_status_text(
                    ""
                )

    # =====================================================
    # STATUS
    # =====================================================

    def set_status(
        self,
        text,
    ):

        colors = {
            "SINK ONLINE": "#5CFF9A",
            "HANDS-FREE": "#FFD76A",
            "LISTENING": "#FF5C8A",
            "THINKING": "#FFD45C",
            "SOLVING": "#FFD45C",
            "SPEAKING": "#FFD76A",
        }

        states = {
            "LISTENING": "listening",
            "THINKING": "thinking",
            "SOLVING": "thinking",
            "SPEAKING": "speaking",
            "HANDS-FREE": "listening",
        }

        color = colors.get(
            text,
            "#5CFF9A",
        )

        state = states.get(
            text,
            "idle",
        )

        self.core.set_state(
            state
        )

        self.status.setText(
            "● " + text
        )

        self.status.setStyleSheet(
            "QLabel { "
            "color: "
            + color
            + "; font-size: 13px; }"
        )

        # "SOLVING..." above the head (any other status turns it off).
        if self.chibi:

            self.chibi.set_solving(
                text == "SOLVING"
            )

        # IMPORTANT:
        # Chibi's special animation gets priority.
        if self.chibi:

            if self.chibi.love_mode:

                return

            if self.chibi.wake_animation:

                return

        self.set_chibi_emotion(
            state
        )

        if self.chibi:

            if text in (
                "LISTENING",
                "THINKING",
                "SPEAKING",
            ):

                self.chibi.set_status_text(
                    text
                )

            elif text not in (
                "SINK ONLINE",
                "HANDS-FREE",
            ):

                self.chibi.set_status_text(
                    ""
                )

    # =====================================================
    # WORKER
    # =====================================================

    def start_worker(
        self,
        text=None,
    ):

        if (
            self.worker is not None
            and self.worker.isRunning()
        ):

            return

        self.quiet_timer.stop()

        # Pause hands-free listener
        if self.listener is not None:

            self.listener.paused = True

        self.worker = Worker(
            self.client,
            self.model_id,
            self.history,
            text,
        )

        self.worker.status.connect(
            self.set_status
        )

        self.worker.heard.connect(
            self.on_heard
        )

        self.worker.reply.connect(
            self.show_reply
        )

        self.worker.minimize.connect(
            self.minimize_to_chibi
        )

        self.worker.restore.connect(
            self.restore_from_chibi
        )

        self.worker.finished.connect(
            self.after_worker
        )

        self.worker.start()

    # -----------------------------------------------------

    def after_worker(self):

        # 3 seconds after SINK finishes -> heart eyes
        self.quiet_timer.start(
            3000
        )

        if (
            self.listener is not None
            and self.listener.running
        ):

            self.listener.paused = False

            self.set_status(
                "HANDS-FREE"
            )

        else:

            self.set_status(
                "SINK ONLINE"
            )

            if self.auto_listen:

                QTimer.singleShot(
                    800,
                    self.listen_message,
                )

    # =====================================================
    # HANDS-FREE
    # =====================================================

    def toggle_handsfree(self):

        # ---------------------------------------------
        # TURN OFF
        # ---------------------------------------------

        if (
            self.listener is not None
            and self.listener.running
        ):

            self.listener.running = False

            self.listener.wait(
                1500
            )

            self.hf_button.setText(
                "🎧 HANDS-FREE"
            )

            self.set_status(
                "SINK ONLINE"
            )

            self.response.setText(
                "Hands-free mode off."
            )

            return

        # ---------------------------------------------
        # TURN ON
        # ---------------------------------------------

        if self.listener is not None:

            self.listener.running = False

            self.listener.wait(
                1500
            )

        self.listener = ListenLoop()

        self.listener.command.connect(
            self.on_voice_command
        )

        self.listener.error.connect(
            self.show_error
        )

        self.listener.quiet.connect(
            self.on_quiet
        )

        self.listener.start()

        self.last_heard = ""

        self.hf_button.setText(
            "⏹ STOP LISTENING"
        )

        self.set_status(
            "HANDS-FREE"
        )

        self.response.setText(
            "Hands-free ON.\n\n"
            "Say SINK first, then your command."
        )

    # =====================================================
    # VOICE COMMAND
    # =====================================================

    def on_voice_command(
        self,
        cmd,
    ):

        # ---------------------------------------------
        # ONLY "SINK"
        # ---------------------------------------------

        # Any heard command keeps SINK awake.
        self.sleep_timer.start()

        if cmd == "__WAKE_ONLY__":

            # FIX:
            # Previously nothing happened if Chibi
            # had never been created.
            self.create_chibi()

            self.chibi.wake_up()

            return

        low = cmd.lower().strip(
            " .!?,"
        )

        # ---------------------------------------------
        # GAME / DICTATION RUNNING
        # (no wake animation spam, no wake word needed)
        # ---------------------------------------------
        if session_active():

            self.start_worker(
                cmd
            )

            return

        # ---------------------------------------------
        # ANGRY
        # ---------------------------------------------
        # ListenLoop removes "SINK" before this function receives cmd.
        if re.search(
            r"\b(i am angry|i'm angry)\b",
            low,
        ):

            self.create_chibi()
            self.chibi.show()
            self.chibi.raise_()
            self.chibi.wake_up()
            self.start_worker(cmd)
            return

        # LOVE
        # ---------------------------------------------
        if re.search(
            LOVE_PATTERN,
            low,
        ):

            self.show_blush()
            self.start_worker(cmd)
            return

        # CUTE
        # ---------------------------------------------
        if re.search(
            CUTE_PATTERN,
            low,
        ):

            self.show_blush()
            self.start_worker(cmd)
            return

        # GO TO SLEEP: turn SINK fully off (mic + chibi)
        # ---------------------------------------------

        if low in (
            "go to sleep",
            "sleep",
            "sleep now",
            "good night",
        ):

            speak_async(
                "Going to sleep. Call me when you need me."
            )

            self.sleep_sink()

            return

        # NORMAL WAKE ANIMATION
        # ---------------------------------------------

        self.create_chibi()

        self.chibi.wake_up()

        # ---------------------------------------------
        # STOP
        # ---------------------------------------------

        if low in (
            "stop listening",
            "stop",
            "go to sleep",
            "sleep",
        ):

            self.toggle_handsfree()

            return

        self.start_worker(
            cmd
        )

    # =====================================================
    # HEARD
    # =====================================================

    def on_heard(
        self,
        text,
    ):

        self.last_heard = text

        low = text.lower()

        # ---------------------------------------------
        # ANGRY
        # ---------------------------------------------

        if re.search(r"\b(i am angry|i'm angry)\b", low):

            self.create_chibi()
            self.chibi.wake_up()
            self.response.setText(
                "You: " + text + "\n\nSINK: Not my problem."
            )
            self.speak_direct("Not my problem.")
            return

        # ---------------------------------------------
        # LOVE
        # ---------------------------------------------

        if re.search(
            LOVE_PATTERN,
            low,
        ):

            self.show_blush()

            self.response.setText(
                "You: "
                + text
                + "\n\n"
                "SINK: Awww, love you too. ♥"
            )

            return

        # ---------------------------------------------
        # CUTE
        # ---------------------------------------------

        if re.search(
            CUTE_PATTERN,
            low,
        ):

            self.show_blush()

            self.response.setText(
                "You: "
                + text
                + "\n\n"
                "SINK: Stop it, you're making me blush!"
            )

            return

        # ---------------------------------------------
        # HELLO
        # ---------------------------------------------

        if re.search(
            r"\b(hello|hi|hey)\b",
            low,
        ):

            self.create_chibi()

            # Don't overwrite love/wake.
            if (
                not self.chibi.love_mode
                and not self.chibi.wake_animation
            ):

                self.chibi.set_emotion(
                    "blush"
                )

                self.chibi.set_status_text(
                    "HELLO ♥"
                )

                self.blush_timer.start(
                    2500
                )

        self.response.setText(
            "You: "
            + text
            + "\n\n"
            "SINK is thinking..."
        )

    def speak_direct(self, text):

        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 190)
            engine.setProperty("volume", 1.0)
            engine.say(text)
            engine.runAndWait()
        except Exception as error:
            print("DIRECT VOICE ERROR:", error)

    # =====================================================
    # REPLY
    # =====================================================

    def show_reply(
        self,
        answer,
    ):

        # IMPORTANT:
        # Do not kill love animation.
        if (
            self.chibi
            and self.chibi.love_mode
        ):

            pass

        else:

            self.heart_timer.stop()

        if answer.startswith(
            "SINK ERROR"
        ):

            # Don't overwrite special animations.
            if (
                self.chibi
                and not self.chibi.love_mode
                and not self.chibi.wake_animation
            ):

                self.set_chibi_emotion(
                    "sad"
                )

        if self.last_heard:

            self.response.setText(
                "You: "
                + self.last_heard
                + "\n\n"
                "SINK: "
                + answer
            )

        else:

            self.response.setText(
                answer
            )

    # =====================================================
    # ERROR
    # =====================================================

    def show_error(
        self,
        message,
    ):

        if (
            self.chibi
            and not self.chibi.love_mode
            and not self.chibi.wake_animation
        ):

            self.set_chibi_emotion(
                "sad"
            )

            self.set_chibi_status(
                "ERROR"
            )

        self.response.setText(
            "SINK ERROR\n\n"
            + message
        )

    # =====================================================
    # BUTTON ACTIONS
    # =====================================================

    def send_message(self):

        text = self.input.text().strip()

        if not text:
            return

        self.input.clear()

        low = text.lower()

        if re.search(r"\b(i am angry|i'm angry)\b", low):

            self.create_chibi()
            self.chibi.wake_up()
            self.response.setText(
                "You: " + text + "\n\nSINK: Not my problem."
            )
            self.speak_direct("Not my problem.")
            return

        # Typed "I love you"
        if re.search(
            LOVE_PATTERN,
            low,
        ):

            self.show_blush()

        if re.search(
            CUTE_PATTERN,
            low,
        ):

            self.show_blush()

        self.start_worker(
            text
        )

    # -----------------------------------------------------

    def listen_message(self):

        self.start_worker(
            None
        )

    # =====================================================
    # CLOSE
    # =====================================================

    def closeEvent(
        self,
        event,
    ):

        if self.listener is not None:

            self.listener.running = False

            self.listener.wait(
                1500
            )

        event.accept()

        # SINK can now sleep with no window visible, so closing
        # the dashboard quits explicitly (same behaviour as before).
        QApplication.quit()


# =========================================================
# SHORTCUT SERVER + TRAY
# =========================================================

LM_STUDIO_PORT = 1234


def ensure_lm_studio():

    """Start LM Studio's local server if it is not running.
    Runs in a background thread, so SINK never waits for it."""

    def _run():

        try:
            with socket.create_connection(
                ("127.0.0.1", LM_STUDIO_PORT),
                timeout=0.5,
            ):
                print("LM STUDIO: already running")
                return
        except OSError:
            pass

        try:
            subprocess.Popen(
                ["lms", "server", "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    0x08000000 if os.name == "nt" else 0
                ),
            )
            print("LM STUDIO: start requested")

        except FileNotFoundError:
            print(
                "LM STUDIO: the 'lms' command was not found. "
                "Start the LM Studio server by hand."
            )

        except Exception as error:
            print("LM STUDIO START ERROR:", error)

    threading.Thread(
        target=_run,
        daemon=True,
    ).start()


class ShortcutBridge(QObject):

    toggle = Signal()
    dashboard = Signal()


def start_shortcut_server(bridge):

    """Blocks on accept(), so it uses no CPU while waiting."""

    if _SINK_SERVER is None:
        print("SHORTCUT TOGGLE UNAVAILABLE: port busy.")
        return

    def serve():

        while True:

            try:
                conn, _ = _SINK_SERVER.accept()
            except OSError:
                return

            with conn:

                try:
                    msg = conn.recv(16)
                except OSError:
                    continue

                if msg.startswith(b"dashboard"):
                    bridge.dashboard.emit()
                elif msg.startswith(b"toggle"):
                    bridge.toggle.emit()

    threading.Thread(
        target=serve,
        daemon=True,
    ).start()


def build_tray(app, window):

    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFC93C"))
    p.drawEllipse(6, 6, 52, 52)
    p.end()

    tray = QSystemTrayIcon(QIcon(pm), app)
    menu = QMenu()

    def add(label, fn):
        act = QAction(label, menu)
        act.triggered.connect(fn)
        menu.addAction(act)

    add("Wake / Sleep SINK", window.hotkey_toggle)
    add("Open dashboard", window.show_dashboard)
    menu.addSeparator()
    add("Friend mode", lambda: persona_set("mode", "friend"))
    add("Assistant mode", lambda: persona_set("mode", "assistant"))
    add("Call me Mam", lambda: persona_set("address", "Mam"))
    add("Call me Sir", lambda: persona_set("address", "Sir"))
    menu.addSeparator()
    add("Quit SINK", window.quit_sink)

    tray.setContextMenu(menu)
    tray.setToolTip("SINK")
    tray.show()

    return tray


# =========================================================
# START SINK
# =========================================================

if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )

    # SINK can sleep with every window hidden; it must not quit then.
    app.setQuitOnLastWindowClosed(
        False
    )

    # Gaming laptop: never compete with games or the browser.
    try:
        psutil.Process().nice(
            psutil.BELOW_NORMAL_PRIORITY_CLASS
        )
    except Exception:
        pass

    ensure_lm_studio()

    window = SinkWindow()

    bridge = ShortcutBridge()
    bridge.toggle.connect(window.hotkey_toggle)
    bridge.dashboard.connect(window.show_dashboard)
    start_shortcut_server(bridge)

    tray = build_tray(app, window)

    if "--dashboard" in sys.argv:

        # Old behaviour: open the full dashboard.
        window.show()

    else:

        # Launched with the shortcut key: chibi only.
        window.create_chibi()
        window.chibi.wake_up()

    # Start listening on launch: no mouse needed.
    QTimer.singleShot(
        1500,
        window.toggle_handsfree,
    )

    window.sleep_timer.start()

    sys.exit(
        app.exec()
    )
