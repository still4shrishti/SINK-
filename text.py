import traceback

print("\n=== STEP 1: MICROPHONE ===")
try:
    import sounddevice as sd
    import numpy as np

    print(sd.query_devices())
    print("Default device (input, output):", sd.default.device)
    print("\nBolo kuch 3 second ke liye...")
    rec = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="float32")
    sd.wait()
    level = float(np.abs(rec).max())
    print("Mic volume level:", round(level, 4))
    if level < 0.01:
        print("FAIL: mic se awaaz nahi aa rahi (mic muted / galat device / permission)")
    else:
        print("PASS: mic kaam kar raha hai")
except Exception:
    print("FAIL: mic error")
    traceback.print_exc()
    rec = None

print("\n=== STEP 2: WHISPER (speech to text) ===")
try:
    from faster_whisper import WhisperModel

    model = WhisperModel("tiny", compute_type="int8")
    if rec is not None:
        segments, _ = model.transcribe(rec.flatten(), language="en")
        text = " ".join(s.text for s in segments).strip()
        print("Whisper ne suna:", repr(text))
    print("PASS: whisper load ho gaya")
except Exception:
    print("FAIL: whisper error")
    traceback.print_exc()

print("\n=== STEP 3: SPEAKER (text to speech) ===")
try:
    import pyttsx3

    engine = pyttsx3.init()
    engine.say("Hello, I am SINK. Voice test successful.")
    engine.runAndWait()
    print("PASS: awaaz aayi? agar nahi aayi to speaker/volume check karo")
except Exception:
    print("FAIL: tts error")
    traceback.print_exc()

print("\nDone. Pura output copy karke mujhe bhej do.")