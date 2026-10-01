import sounddevice as sd
import wave
from faster_whisper import WhisperModel

model = WhisperModel("tiny", compute_type="int8")

def listen():
    print("🎤 Listening...")

    recording = sd.rec(
        int(5 * 16000),
        samplerate=16000,
        channels=1,
        dtype="int16"
    )
    sd.wait()

    with wave.open("voice.wav", "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(recording.tobytes())

    segments, info = model.transcribe("voice.wav")

    text = ""
    for segment in segments:
        text += segment.text

    return text.strip()


while True:
    text = listen()
    print("You:", text)

    if text.lower() == "exit":
        break