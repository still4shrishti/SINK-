# SINK: Local Voice AI Desktop Assistant

SINK is a Jarvis-style voice assistant that runs **fully on your own PC**.
Say "SINK" and give a command, and it will open apps, click, scroll, type, set reminders, play games and chat with you.
It has a small animated robot face that reacts to what you say.

- **Brain:** Qwen 3, running locally through LM Studio
- **Ears:** faster-whisper (speech to text, on CPU)
- **Voice:** pyttsx3 (text to speech)
- **Face and window:** PySide6 (Qt)
- **Hands:** pyautogui (mouse and keyboard control)

Your voice and notes are not sent to any cloud service. Everything stays on your computer.

---

## Features

### Talk to it
- Wake word: say **"SINK"** (also hears "sync", "think", "zinc"). Example: *"SINK open Chrome"*
- Hands-free listening toggle, or type commands in the text box
- Answers general questions using Qwen 3 from LM Studio
- Chain commands: *"press click and then scroll down"*

### Control the PC
| Say | What happens |
|---|---|
| open Chrome / YouTube / notepad | Opens apps and websites |
| click, double click, right click | Mouse clicks |
| scroll down / up / top / bottom | Scrolling |
| move mouse right 200 | Moves the cursor |
| press enter, press escape | Presses keys |
| copy, paste, undo, save, new tab, close window | Keyboard shortcuts |
| volume up / down / mute | Volume control |
| search for cats | Google search |
| screenshot, full screen, next tab | More shortcuts |
| CPU, RAM, battery, disk, time | System info |

### Understands different ways of saying the same thing
"press click", "tap", "left click" and "click it" all mean **click**.
"hit enter", "press return" and "enter key" all mean **enter**.
"go down", "scroll down" and "scrawl down" all mean **scroll down**.
Filler words like *please*, *can you*, *kar do* and *karo* are ignored, and a few Hindi words (*kholo*, *neeche*, *upar*) work too.

### Smart extras
- **Rock Paper Scissors:** "SINK play stone paper scissors", then just say rock, paper or scissors. Say "stop game" for the final score.
- **Tasks and notes:** "add task finish assignment", "read my tasks", "complete task 2"
- **Reminders and timers:** "remind me in 10 minutes to drink water", "set a timer for 5 minutes"
- **Dictation mode:** "start typing", then whatever you say is typed into any app. Say "new line" for enter and "stop typing" to finish.
- **Clipboard helper:** "explain what I copied", "summarize clipboard", "translate what I copied to Hindi"
- **Study helper:** "quiz me on binary trees"

### Expressions
- Blushes when you say **"I love you"** or **"SINK you are so cute"**
- Shows **heart eyes** when you stop talking for 3 seconds
- Dances and reacts when it wakes up

---

## Requirements

- **Windows 10 or 11** (uses Windows-only features such as `start` and PowerShell)
- **Python 3.10 or newer**
- **LM Studio** with a **Qwen 3** model downloaded
- A working microphone and speakers

### Python packages

Create and activate a virtual environment (recommended):

```bash
python -m venv venv
venv\Scripts\activate
```

Then install every module that `gui.py` uses:

```bash
pip install PySide6 faster-whisper openai numpy psutil pyautogui sounddevice pyttsx3 pyperclip pywin32
```

Or install them all at once from the file included with the project:

```bash
pip install -r requirements.txt
```

| Module | Used for |
|---|---|
| `PySide6` | Window, robot face and animations |
| `faster-whisper` | Speech to text |
| `openai` | Talking to Qwen 3 in LM Studio |
| `numpy` | Handling microphone audio |
| `sounddevice` | Recording from the microphone |
| `pyttsx3` | SINK's voice |
| `pyautogui` | Mouse, keyboard and typing control |
| `psutil` | CPU, RAM, battery and disk info |
| `pyperclip` | Clipboard helper |
| `pywin32` | Lets reminders speak from the background |

---

## How to run

1. Open **LM Studio**, load a **Qwen 3** model, and start the **Local Server**.
   It should be running at `http://127.0.0.1:1234`.
2. Put `gui.py` in a folder and run:
   ```bash
   python gui.py
   ```
3. The first run downloads the Whisper `base` model, so it needs internet once. After that it works offline.
4. Say **"SINK"** followed by your command, or type in the box.

SINK picks the model that is loaded in LM Studio automatically, so you don't need to set a model name in the code.

---

## Files SINK creates

| File | What it stores |
|---|---|
| `sink_tasks.json` | Your tasks and notes |

It is a plain text file in the same folder as `gui.py`. You can open or delete it any time.

---

## Safety

- Commands that touch protected places or sensitive data (for example `system32`, `regedit`, passwords, `shutdown`, deleting files) are blocked.
- Moving the mouse to a screen corner stops `pyautogui` immediately (the failsafe is on).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| SINK chats but gives errors | Check that the LM Studio server is started and a Qwen 3 model is loaded |
| It doesn't hear me | Check the microphone in Windows settings and say "SINK" clearly before the command |
| Wrong words are heard | Use a headset mic and speak in a quiet room |
| Hindi isn't understood well | Whisper is set to English. Hindi words in commands may be misheard |
| Dictation types into the wrong place | Click the target window first, then say "start typing" |
| Typing doesn't work in some apps | Apps running as administrator can ignore `pyautogui` |

---

## Known limits

- Dictation types English only
- Speech recognition runs on CPU with the `base` model (falls back to `tiny`), so it is not instant
- Commands are matched with rules, so very unusual phrasing may not be understood

## Ideas for later

- **Memory:** let SINK remember things you tell it (for example "remember that my exam is on Monday") and use them in chats
- Noise reduction and a "learn my voice" mode so it only responds to you
- Better Hindi support
- More apps and websites in its list

