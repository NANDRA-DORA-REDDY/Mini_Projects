"""voice.py - Amvi voice. Local speech-to-text (faster-whisper) and text-to-speech.
Two kinds of voices:
  * offline  - the Windows voices installed on your PC (pyttsx3)
  * neural   - natural Microsoft voices through edge-tts (needs internet; the spoken text is sent online)
Speech runs in a small child process so it can be interrupted instantly (stop_speaking)."""
import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import glob
import json
import re
import subprocess
import sys
import tempfile

import numpy as np
import pyttsx3
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
SPEECH_SPEED = 185          # words per minute (change with: voice speed 160)
VOICE_INDEX = 0             # offline voice number (see: voice list)
NEURAL_NAME = None          # e.g. "en-IN-NeerjaNeural"; None = use the offline voice
THRESHOLD = 0.012           # mic loudness that counts as speech; raise if it never stops,
                            # lower if it misses your voice
MAX_SECONDS = 12            # longest single command
SILENCE_SECONDS = 1.2       # stop after this much quiet

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amvi_voice_settings.json")
OLD_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_settings.json")

# short name -> (Microsoft voice id, description)
NEURAL = {
    "neerja":  ("en-IN-NeerjaNeural",  "Indian English, female"),
    "prabhat": ("en-IN-PrabhatNeural", "Indian English, male"),
    "aria":    ("en-US-AriaNeural",    "US English, female"),
    "jenny":   ("en-US-JennyNeural",   "US English, female, friendly"),
    "guy":     ("en-US-GuyNeural",     "US English, male"),
    "sonia":   ("en-GB-SoniaNeural",   "British English, female"),
    "ryan":    ("en-GB-RyanNeural",    "British English, male"),
    "shruti":  ("te-IN-ShrutiNeural",  "Telugu, female"),
    "mohan":   ("te-IN-MohanNeural",   "Telugu, male"),
    "swara":   ("hi-IN-SwaraNeural",   "Hindi, female"),
    "madhur":  ("hi-IN-MadhurNeural",  "Hindi, male"),
}

# What Whisper may write when you say "Hey Amvi". Add your own with:  voice wake add <word>
WAKE_BASE = [r"amvi", r"am\s?v(?:i|ee|y)", r"am\s?v", r"anvi", r"ambi", r"aamvi", r"emvi"]
_EXTRA_WAKE = []


def _build_wake():
    alts = WAKE_BASE + [re.escape(w) for w in _EXTRA_WAKE]
    return re.compile(r"\b(?:(?:hey|hi|ok|okay)[\s,]+)?(?:" + "|".join(alts) + r")\b", re.I)


WAKE_RE = _build_wake()

_model = None
_proc = None
_stopped = False


# ---------------- Saved settings ----------------
def _load():
    global VOICE_INDEX, SPEECH_SPEED, NEURAL_NAME, _EXTRA_WAKE, WAKE_RE
    try:
        path = SETTINGS_FILE if os.path.exists(SETTINGS_FILE) else OLD_SETTINGS_FILE
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        VOICE_INDEX = int(d.get("index", VOICE_INDEX))
        SPEECH_SPEED = int(d.get("speed", SPEECH_SPEED))
        NEURAL_NAME = d.get("neural") or None
        _EXTRA_WAKE = [str(w) for w in d.get("wake_extra", [])]
        WAKE_RE = _build_wake()
    except Exception:
        pass


def _save():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"index": VOICE_INDEX, "speed": SPEECH_SPEED, "neural": NEURAL_NAME, "wake_extra": _EXTRA_WAKE}, f)
    except Exception:
        pass


_load()


def _get_model():
    global _model
    if _model is None:
        print("Amvi: Loading speech model (first time downloads ~150 MB)...")
        _model = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _model


# ---------------- Speaking ----------------
def _clean(text):
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    text = re.sub(r"[*#`_]", "", text)
    return text.strip()[:350]          # keep spoken replies short


_LOCAL_CODE = "\n".join([
    "import sys, pyttsx3",
    "e = pyttsx3.init()",
    "v = e.getProperty('voices')",
    "i = int(sys.argv[2])",
    "if v: e.setProperty('voice', v[min(i, len(v) - 1)].id)",
    "e.setProperty('rate', int(sys.argv[3]))",
    "e.say(sys.argv[1])",
    "e.runAndWait()",
])

_NEURAL_CODE = "\n".join([
    "import sys, os, asyncio, tempfile, ctypes",
    "import edge_tts",
    "text, voice, rate = sys.argv[1], sys.argv[2], sys.argv[3]",
    "path = os.path.join(tempfile.gettempdir(), 'amvi_tts_%d.mp3' % os.getpid())",
    "asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(path))",
    "mci = ctypes.windll.winmm.mciSendStringW",
    "mci('open \"%s\" type mpegvideo alias amvi' % path, None, 0, 0)",
    "mci('play amvi wait', None, 0, 0)",
    "mci('close amvi', None, 0, 0)",
    "os.remove(path)",
])


def _run(code, *args):
    global _proc
    _proc = subprocess.Popen([sys.executable, "-c", code, *args],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return _proc.wait()


def _edge_rate():
    pct = round((SPEECH_SPEED - 185) / 185 * 100)
    return f"{max(-50, min(100, pct)):+d}%"


def speak(text):
    """Speak and wait until finished (or until stop_speaking() is called)."""
    global _stopped
    text = _clean(text)
    if not text:
        return
    _stopped = False
    try:
        if NEURAL_NAME:
            for old in glob.glob(os.path.join(tempfile.gettempdir(), "amvi_tts_*.mp3")):
                try:
                    os.remove(old)
                except OSError:
                    pass
            code = _run(_NEURAL_CODE, text, NEURAL_NAME, _edge_rate())
            if code == 0 or _stopped:
                return
            print("Amvi: (neural voice unavailable - offline? using the Windows voice)")
        _run(_LOCAL_CODE, text, str(VOICE_INDEX), str(SPEECH_SPEED))
    except Exception as e:
        print("Amvi: (voice output failed)", e)


def stop_speaking():
    global _stopped
    p = _proc
    if p is not None and p.poll() is None:
        _stopped = True
        try:
            p.kill()
        except Exception:
            pass
        return True
    return False


def is_speaking():
    return _proc is not None and _proc.poll() is None


def list_voices():
    engine = pyttsx3.init()
    local = [f"  {i}: {v.name}" for i, v in enumerate(engine.getProperty("voices"))]
    engine.stop()
    neural = [f"  {k:8} {desc}" for k, (_vid, desc) in NEURAL.items()]
    now = f"neural voice {NEURAL_NAME}" if NEURAL_NAME else f"offline voice #{VOICE_INDEX}"
    return ("Offline voices (installed in Windows):\n" + "\n".join(local) +
            "\n\nNeural voices (very natural, need internet):\n" + "\n".join(neural) +
            "\n\nChoose one:  voice 1  |  voice neerja  |  voice use en-IN-NeerjaNeural  |  voice local"
            f"\nCurrently using: {now}, speed {SPEECH_SPEED}")


def set_speed(wpm):
    global SPEECH_SPEED
    SPEECH_SPEED = max(100, min(300, int(wpm)))
    _save()


def set_voice(index):
    """Pick an offline Windows voice by number."""
    global VOICE_INDEX, NEURAL_NAME
    VOICE_INDEX, NEURAL_NAME = int(index), None
    _save()


def _wake_command(rest):
    """voice wake add <word> | list | reset.  Returns ok=False so callers do not play the voice test line."""
    global _EXTRA_WAKE, WAKE_RE
    if rest.startswith("add "):
        w = rest[4:].strip()
        if not re.fullmatch(r"[a-z][a-z ]{1,19}", w):
            return False, "Use plain letters, for example: voice wake add ami"
        if w not in _EXTRA_WAKE:
            _EXTRA_WAKE.append(w)
        WAKE_RE = _build_wake(); _save()
        return False, f"Added '{w}' as a wake word. Turn hands-free on and say 'Hey {w}' to test."
    if rest == "reset":
        _EXTRA_WAKE = []; WAKE_RE = _build_wake(); _save()
        return False, "Wake words reset to the defaults."
    return False, ("Wake word: Amvi (also heard as amv, anvi, ambi, emvi, am vee...). Extra words: "
                   + (", ".join(_EXTRA_WAKE) or "none") + ".  Add one with: voice wake add <word>")


def choose(arg):
    """Pick any voice from text: a number, a short name (neerja), 'use <voice id>' or 'local'.
    Returns (ok, message)."""
    global NEURAL_NAME, VOICE_INDEX
    a = arg.strip().lower()
    if a == "wake" or a.startswith("wake "):
        return _wake_command(a[4:].strip())
    if a in ("local", "offline", "default", "windows"):
        NEURAL_NAME = None
        _save()
        return True, "Using the offline Windows voice."
    if a.isdigit():
        set_voice(int(a))
        return True, f"Using offline voice #{a}."
    name, desc = None, ""
    if a.startswith("use "):
        cand = arg.strip()[4:].strip()
        if re.fullmatch(r"[a-z]{2,3}-[A-Za-z]{2,4}-\w+Neural", cand):
            name, desc = cand, "custom neural voice"
    else:
        for key, (vid, d) in NEURAL.items():
            if a == key or a in d.lower():
                name, desc = vid, f"{key} ({d})"
                break
    if not name:
        return False, "I don't know that voice. Say 'voice list' to see the choices."
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False, "Neural voices need one more package. Run: pip install edge-tts"
    NEURAL_NAME = name
    _save()
    return True, f"Using neural voice {desc}. (Needs internet; falls back to the Windows voice when offline.)"


# ---------------- Listening ----------------
def listen(max_seconds=None, idle_seconds=None):
    """Record until you stop talking, then return the text ('' if nothing heard).
    idle_seconds: give up early if nobody starts talking within this time (used by the wake-word loop)."""
    _get_model()                         # load first, so you never speak before it's ready
    chunk = int(SAMPLE_RATE * 0.1)
    frames, started, quiet, waited = [], False, 0, 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        for _tick in range(int((max_seconds or MAX_SECONDS) / 0.1)):
            data, _overflow = stream.read(chunk)
            frames.append(data)
            if float(np.abs(data).mean()) > THRESHOLD:
                started, quiet = True, 0
            elif started:
                quiet += 1
                if quiet >= SILENCE_SECONDS * 10:
                    break
            else:
                waited += 1
                if idle_seconds and waited >= idle_seconds * 10:
                    return ""

    if not started:
        return ""

    audio = np.concatenate(frames).flatten()
    segments, _info = _get_model().transcribe(audio, language="en", vad_filter=True)
    return " ".join(s.text for s in segments).strip()
