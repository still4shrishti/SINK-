# SINK 🤖

**SINK** is a local AI assistant designed to run on your computer using open-source/open-weight AI.

It combines a local LLM, voice recognition, text-to-speech, a graphical interface, and computer-control tools into one assistant.

> **Goal:** Build a private, locally running AI assistant that can understand natural language and eventually interact with the computer like a human user.

---

## ✨ Features

### 🧠 Local AI

SINK uses **Qwen3** as its local AI brain.

The model runs locally through **LM Studio**, so normal conversations and AI reasoning do not require sending prompts to a cloud AI service.

SINK can answer questions and solve problems such as:

* Mathematics
* Integration
* Physics
* General questions
* Programming questions
* Explanations and reasoning
* Natural-language conversations

---

### 🎤 Voice Input

SINK can listen to your voice using:

* `faster-whisper`
* `sounddevice`

The pipeline is:

```text
Microphone
     ↓
Speech
     ↓
Faster-Whisper
     ↓
Text
     ↓
Qwen3
```

---

### 🔊 Voice Output

SINK can convert its responses into speech using a text-to-speech engine.

The goal is to make the interaction feel more like talking to an actual assistant instead of using a traditional chatbot.

---

### ✨ Graphical Interface

SINK includes a futuristic graphical interface built with **PySide6**.

Current interface elements include:

* Animated AI core
* Particle effects
* Rotating rings
* SINK status indicator
* Chat/response area
* Text input
* Processing state

---

## 🖥️ Current Architecture

```text
                    ┌──────────────┐
                    │ Microphone   │
                    └──────┬───────┘
                           ↓
                    ┌──────────────┐
                    │   Whisper    │
                    │ Speech → Text│
                    └──────┬───────┘
                           ↓
                    ┌──────────────┐
                    │    Qwen3     │
                    │ Local LLM    │
                    └──────┬───────┘
                           ↓
                    ┌──────────────┐
                    │    SINK      │
                    │ Tool System  │
                    └──────┬───────┘
                           ↓
              ┌─────────────────────────┐
              │ Computer Control Layer  │
              └─────────────────────────┘
                           ↓
                 Windows / Applications
```

---

# 🚀 Installation

## Requirements

* Windows
* Python 3.13
* LM Studio
* Qwen3
* Microphone
* Git

Python 3.13 is currently recommended because some audio/AI dependencies may not yet provide compatible wheels for newer Python versions.

---

## 1. Clone the repository

```bash
git clone https://github.com/YOUR-USERNAME/sink.git
cd sink
```

---

## 2. Create a virtual environment

```powershell
py -3.13 -m venv .venv
```

Activate it:

```powershell
.venv\Scripts\Activate.ps1
```

You should see:

```text
(.venv)
```

in your terminal.

---

## 3. Install dependencies

```powershell
pip install -r requirements.txt
```

If you don't have a `requirements.txt` yet:

```powershell
pip install PySide6 openai faster-whisper sounddevice
```

---

# 🧠 Setting Up Qwen3

Install **LM Studio** and download a Qwen3 model.

Open LM Studio and:

```text
Load Qwen3
     ↓
Local Model API
     ↓
Start Server
```

The default local API endpoint used by SINK is:

```text
http://localhost:1234/v1
```

SINK automatically asks the local API for the available model and uses it.

---

# ▶️ Running SINK

Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

Start LM Studio's Local Model API.

Then run:

```powershell
python gui.py
```

For the voice-enabled version, run the corresponding voice entry point:

```powershell
python main.py
```

Your exact entry-point filename may change as the project develops.

---

# 🎤 Voice Pipeline

The intended voice pipeline is:

```text
You speak
    ↓
Microphone
    ↓
Faster-Whisper
    ↓
Text command
    ↓
Qwen3
    ↓
Tool selection
    ↓
Computer action
    ↓
SINK response
    ↓
Text-to-Speech
    ↓
SINK speaks
```

---

# 💻 Planned Computer Control

One of the main goals of SINK is to allow the AI to interact with the computer.

For example:

```text
"Open Chrome"
        ↓
SINK launches Chrome
```

Then eventually:

```text
"Search YouTube for calculus lectures"
        ↓
Open browser
        ↓
Click/search field
        ↓
Type query
        ↓
Press Enter
```

Other planned capabilities include:

* Opening applications
* Opening websites
* Typing text
* Clicking buttons
* Scrolling
* Keyboard shortcuts
* Reading select

