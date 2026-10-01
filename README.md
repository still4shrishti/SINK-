# SINK – Local Voice AI Desktop Assistant

SINK is a Jarvis-like assistant that runs fully on your own PC. It listens through your microphone, understands speech with **faster-whisper**, chats using a local model served by **LM Studio**, speaks back with **pyttsx3**, and can control your desktop (open apps, scroll, click, type, etc.) with **pyautogui**. The GUI is built with **PySide6** and includes an animated chibi robot.

> **Platform:** Windows (uses `pythoncom`, Windows app launch names such as `msedge`, `calc`, `ms-settings:`).

---

## Project Files

| File | Purpose |
|------|---------|
| `gui.py` | **Main app.** Qt window, wake word ("SINK"), voice + PC commands, TTS, animated chibi robot. |
| `main.py` | Minimal terminal chatbot that talks to the LM Studio server (text only, type `exit` to quit). |
| `voice.py` | Simple speech-to-text test: records 5 seconds from the mic, saves `voice.wav`, transcribes it with Whisper (`tiny`). |
| `text.py` | 3-step diagnostic: tests the **microphone**, **Whisper**, and **speaker (TTS)** and prints PASS/FAIL for each. |
| `voice.wav` | Temporary recording created by `voice.py`. Safe to delete. |
| `requirements.txt` | Python dependencies. |

---

## Requirements

- Windows 10/11
- Python 3.10+
- [LM Studio](https://lmstudio.ai/) with a model loaded and the **local server running** on port `1234`
- A working microphone and speakers

---

## Installation

```bash
# 1. (Optional) create a virtual environment
python -m venv venv
venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt


> `psutil` is imported by `gui.py` for CPU / RAM / battery / disk status. `pywin32` provides `pythoncom`, which is used to make text-to-speech work inside threads. Consider adding both to `requirements.txt`.

---

## Setup LM Studio

1. Open LM Studio and load any chat model.
2. Go to the **Local Server** tab and start the server (default: `http://localhost:1234/v1`).
3. SINK automatically picks the first model returned by the server.

---

## How to Run

**Full GUI assistant**
```bash
python gui.py
```

**Terminal chat (no voice)**
```bash
python main.py
```

**Test speech-to-text only**
```bash
python voice.py
```

**Run the hardware diagnostic (mic → Whisper → speaker)**
```bash
python text.py
```
Run this first if SINK can't hear you or can't speak.

---

## Using the GUI (`gui.py`)

- **🎤 SPEAK** – listen once and respond.
- **🎧 HANDS-FREE** – keep listening continuously for the wake word.
- **SEND** – send the typed message.
- **Keep listening after SINK speaks** – auto-listen checkbox.
- **🤖 MINIMIZE TO CHIBI** – shrinks the window into a small floating robot.

### Wake word

Say **"SINK"** (also "hey sink", "ok sink", etc.) followed by your command. Saying only "SINK" wakes the robot.

### Example voice commands

| Category | Examples |
|----------|----------|
| Open apps | `open Chrome`, `open Notepad`, `open Calculator`, `open VS Code`, `open Task Manager`, `open Settings` |
| Open sites | `open YouTube` (or `yt`), `open Gmail`, `open GitHub`, `open ChatGPT` |
| Search | `search for <query>`, `google <query>` |
| Mouse | `click`, `double click`, `right click`, `move mouse right 200` |
| Scroll | `scroll down`, `scroll up`, `scroll to top`, `scroll to bottom`, `page down`, `page up` |
| Keyboard | `type hello`, `press enter` |
| Shortcuts | `copy`, `paste`, `cut`, `select all`, `undo`, `redo`, `save`, `new tab`, `close tab`, `switch window`, `go back`, `refresh`, `zoom in`, `minimize window`, `show desktop` |
| System info | `CPU usage`, `RAM usage`, `disk usage`, `system status`, `what time is it` |
| Chained steps | `open Chrome and then open YouTube`, `scroll down then click` |
| Control | `stop listening` / `go to sleep` |
| Chat | Anything else goes to the LM Studio model as a normal conversation. |

---

## Safety

- `pyautogui.FAILSAFE = True` – move the mouse to the top-left corner of the screen to abort any automation.
- Requests containing sensitive terms (e.g. `system32`, `password`, `registry`, `delete file`, `shutdown`, `restart`) are **blocked**.

---

## Configuration

| Setting | Where | Default |
|---------|-------|---------|
| LM Studio URL | `gui.py` (`SinkWindow`), `main.py` | `http://127.0.0.1:1234/v1` (gui) / `http://localhost:1234/v1` (main) |
| Whisper model | `gui.py` (top-level `stt`) | `base` on CPU, int8 (falls back to `tiny`) |
| Wake-word pattern | `gui.py` → `WAKE_PATTERN` | sink / sync / think / zinc … |
| Apps list | `gui.py` → `APPS` | Chrome, Edge, Notepad, etc. |
| Websites list | `gui.py` → `SITES` | YouTube, Google, Gmail, GitHub, ChatGPT |
| Shortcuts | `gui.py` → `SHORTCUTS` | see table above |

To add a new app or website, just add an entry to the `APPS` or `SITES` dictionary in `gui.py`.

---

## Troubleshooting

- **Cannot connect / SINK ERROR** – make sure the LM Studio local server is running on port 1234 with a model loaded.
- **No voice input** – run `python text.py` and check the microphone step; verify the Windows default input device and mic permissions.
- **No voice output** – check speaker volume and that `pyttsx3` (and `pywin32`) are installed.
- **Wake word not detected** – speak clearly and close to the mic; Whisper may mishear "SINK", so several similar words are already accepted.
- **First launch is slow** – Whisper downloads its model the first time.

---

## Tech Stack

Python · PySide6 · faster-whisper · sounddevice · numpy · pyttsx3 · pyautogui · psutil · OpenAI-compatible client (LM Studio)
