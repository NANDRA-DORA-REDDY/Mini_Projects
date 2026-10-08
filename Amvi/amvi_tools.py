"""tools.py - every action Amvi can perform. The LLM never runs code; it only
picks a tool name from TOOLS. To add a feature: write a function + @tool."""
import json, os, shutil, subprocess, threading, time, urllib.parse, webbrowser
from datetime import datetime
from pathlib import Path

import psutil
import requests

ALLOW_DESTRUCTIVE = False          # delete tools stay disabled until you set True
OLD_WORKSPACE = Path(r"D:\JarvisWorkspace")
WORKSPACE = Path(r"D:\AmviWorkspace")
if OLD_WORKSPACE.exists() and not WORKSPACE.exists():
    shutil.copytree(OLD_WORKSPACE, WORKSPACE)        # one-time copy of your memory, notes, deadlines...
WORKSPACE.mkdir(parents=True, exist_ok=True)
MEMORY_FILE = WORKSPACE / "memory.json"

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OLLAMA_URL = "http://localhost:11434/api/generate"
CODER_MODEL = "qwen2.5-coder:7b"
FALLBACK_MODEL = "llama3.2:latest"
LAST_REPLY = ""                    # jarvis.py updates this so follow-ups have context


def open_chrome(url):
    print(f"Amvi: Opening {url}...")
    if os.path.exists(CHROME_PATH):
        subprocess.Popen([CHROME_PATH, url])
    else:
        webbrowser.open(url)  # fallback: default browser


TOOLS = {}


def tool(desc, args="", dangerous=False, destructive=False):
    def deco(fn):
        TOOLS[fn.__name__] = dict(fn=fn, desc=desc, args=args,
                                  dangerous=dangerous, destructive=destructive)
        return fn
    return deco


def _path(p):
    p = Path(os.path.expandvars(p)).expanduser()
    return p if p.is_absolute() else WORKSPACE / p


# ---------------- Web ----------------
SITES = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "github": "https://github.com/search?q={}",
    "stackoverflow": "https://stackoverflow.com/search?q={}",
    "w3schools": "https://www.google.com/search?q=site:w3schools.com+{}",
    "hackerrank": "https://www.google.com/search?q=site:hackerrank.com+{}",
}
HOMES = {"google": "https://www.google.com", "youtube": "https://www.youtube.com",
         "w3schools": "https://www.w3schools.com", "hackerrank": "https://www.hackerrank.com",
         "github": "https://github.com", "stackoverflow": "https://stackoverflow.com"}


@tool("Open Google")
def open_google(): open_chrome(HOMES["google"]); return "Opening Google."
@tool("Open YouTube")
def open_youtube(): open_chrome(HOMES["youtube"]); return "Opening YouTube."
@tool("Open W3Schools")
def open_w3schools(): open_chrome(HOMES["w3schools"]); return "Opening W3Schools."
@tool("Open HackerRank")
def open_hackerrank(): open_chrome(HOMES["hackerrank"]); return "Opening HackerRank."


@tool("Search Google", '{"query": str}')
def search_google(query=""): return search_site("google", query)


@tool("Search a site: google, youtube, github, stackoverflow, w3schools, hackerrank",
      '{"site": str, "query": str}')
def search_site(site, query=""):
    if not query: return "I need something to search for."
    url = SITES.get(site.lower(), SITES["google"]).format(urllib.parse.quote_plus(query))
    open_chrome(url)
    return f"Searching {site} for {query}."


@tool("Open a website by URL", '{"url": str}')
def open_website(url):
    if not url.startswith("http"): url = "https://" + url
    open_chrome(url); return f"Opening {url}."


# ---------------- Windows control (whitelist only) ----------------
APPS = {  # name: (launch command, process name for closing)
    "calculator": ("calc", "CalculatorApp.exe"), "notepad": ("notepad", "notepad.exe"),
    "paint": ("mspaint", "mspaint.exe"), "vscode": ("code", "Code.exe"),
    "chrome": ("chrome", "chrome.exe"), "explorer": ("explorer", None),
    "settings": ("ms-settings:", None), "control panel": ("control", None),
    "task manager": ("taskmgr", "Taskmgr.exe"), "spotify": ("spotify", "Spotify.exe"),
    "discord": ("discord", "Discord.exe"),
}


def _app(name):
    name = (name.lower().strip().replace("vs code", "vscode").replace("visual studio code", "vscode")
            .replace("google chrome", "chrome").replace("ms paint", "paint"))
    return name if name in APPS else None


@tool("Open an application. Names: " + ", ".join(APPS), '{"app": str}')
def open_app(app):
    key = _app(app)
    if not key: return f"I don't know the app '{app}'. Known: {', '.join(APPS)}"
    subprocess.Popen(f'start "" {APPS[key][0]}', shell=True)  # command comes from whitelist
    return f"Opening {key}."


@tool("Close an application", '{"app": str}', dangerous=True)
def close_app(app):
    key = _app(app)
    if not key or not APPS[key][1]: return "I can't close that app."
    subprocess.run(["taskkill", "/IM", APPS[key][1], "/F"], capture_output=True)
    return f"Closed {key}."


@tool("Restart an application", '{"app": str}', dangerous=True)
def restart_app(app):
    close_app(app); time.sleep(1.5); return open_app(app)


@tool("Lock the computer")
def lock_computer():
    import ctypes; ctypes.windll.user32.LockWorkStation(); return "Locked."


@tool("Shut down the computer", dangerous=True)
def shutdown_computer():
    subprocess.run(["shutdown", "/s", "/t", "30"]); return "Shutting down in 30s (run 'shutdown /a' to cancel)."


@tool("Restart the computer", dangerous=True)
def restart_computer():
    subprocess.run(["shutdown", "/r", "/t", "30"]); return "Restarting in 30s (run 'shutdown /a' to cancel)."


@tool("Put the computer to sleep", dangerous=True)
def sleep_computer():
    subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True); return "Sleeping."


@tool("Press a media/volume key: volumeup, volumedown, volumemute, playpause, nexttrack, prevtrack",
      '{"key": str}')
def media_key(key):
    import pyautogui
    if key not in ("volumeup", "volumedown", "volumemute", "playpause", "nexttrack", "prevtrack"):
        return "Unknown media key."
    pyautogui.press(key); return f"Done: {key}."


@tool("Take a screenshot")
def screenshot():
    from PIL import ImageGrab
    p = WORKSPACE / f"screenshot_{int(time.time())}.png"
    ImageGrab.grab().save(p)
    return f"Saved {p}"


# ---------------- Files (relative paths live inside WORKSPACE) ----------------
@tool("Create a folder", '{"path": str}')
def create_folder(path): _path(path).mkdir(parents=True, exist_ok=True); return f"Created {_path(path)}"


@tool("Create a text file", '{"path": str, "content": str}')
def create_file(path, content=""):
    p = _path(path); p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists(): return "File already exists; not overwriting."
    p.write_text(content, encoding="utf-8"); return f"Created {p}"


@tool("Read a text file", '{"path": str}')
def read_file(path): return _path(path).read_text(encoding="utf-8", errors="ignore")[:3000]


@tool("List files in a folder", '{"path": str}')
def list_dir(path="."): return "\n".join(x.name for x in sorted(_path(path).iterdir())[:50]) or "(empty)"


@tool("Find files by pattern like *.py (searches home folder)", '{"pattern": str}')
def find_files(pattern):
    out = []
    for p in Path.home().rglob(pattern):
        if any(s in p.parts for s in ("AppData", "node_modules", ".git", "venv", ".venv")): continue
        out.append(str(p))
        if len(out) >= 15: break
    return "\n".join(out) or "No matches."


@tool("Open a file or folder", '{"path": str}')
def open_path(path): os.startfile(str(_path(path))); return f"Opened {_path(path)}"


@tool("Copy a file", '{"src": str, "dst": str}')
def copy_file(src, dst): shutil.copy2(_path(src), _path(dst)); return "Copied."


@tool("Delete a file", '{"path": str}', dangerous=True, destructive=True)
def delete_file(path): _path(path).unlink(); return "Deleted."


@tool("Free disk space on C: and D:")
def disk_space():
    out = []
    for d in ("C:\\", "D:\\"):
        try:
            u = psutil.disk_usage(d)
            out.append(f"{d} {u.free/1e9:.0f} GB free of {u.total/1e9:.0f} GB")
        except Exception:
            pass
    return " | ".join(out) or "Could not read disk info."


# ---------------- System monitor ----------------
@tool("CPU, RAM, battery status")
def system_status():
    b = psutil.sensors_battery()
    bat = f"{b.percent:.0f}% ({'charging' if b.power_plugged else 'on battery'})" if b else "n/a"
    return (f"CPU {psutil.cpu_percent(interval=0.5)}%, RAM {psutil.virtual_memory().percent}% "
            f"used, Battery {bat}")


@tool("NVIDIA GPU usage and temperature")
def gpu_status():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
        return "GPU (util, temp C, VRAM MB): " + r.stdout.strip()
    except Exception:
        return "nvidia-smi not available."


# ---------------- Memory + productivity ----------------
def _mem():
    m = json.loads(MEMORY_FILE.read_text()) if MEMORY_FILE.exists() else {}
    if "jarvis project" in m:                      # your old saved name keeps working
        m.setdefault("amvi project", m["jarvis project"])
    return m


@tool("Remember something, e.g. key='amvi project', value='D:\\Projects\\Jarvis'",
      '{"key": str, "value": str}')
def remember(key, value):
    m = _mem(); m[key.lower()] = value; MEMORY_FILE.write_text(json.dumps(m, indent=2)); return f"Remembered {key}."


@tool("Recall a remembered value", '{"key": str}')
def recall(key): return _mem().get(key.lower(), "I don't remember that.")


@tool("Open a remembered path/project", '{"key": str}')
def open_saved(key):
    v = _mem().get(key.lower())
    if not v: return "I don't remember that."
    os.startfile(v); return f"Opening {v}"


@tool("Start a timer", '{"minutes": number, "label": str}')
def set_timer(minutes, label="Timer"):
    def ring():
        time.sleep(float(minutes) * 60); print(f"\n[Amvi] \u23f0 {label} finished!\nYOU: ", end="")
    threading.Thread(target=ring, daemon=True).start()
    return f"{label} set for {minutes} minutes."


@tool("Add a note", '{"text": str}')
def add_note(text):
    with open(WORKSPACE / "notes.txt", "a", encoding="utf-8") as f: f.write(text + "\n")
    return "Note saved."


# ---------------- Coding brain (v4.5) ----------------
@tool("Programming help: write, explain, debug or improve code, or answer a technical question",
      '{"question": str}')
def ask_coder(question):
    context = f"Previous answer, for context:\n{LAST_REPLY[:1500]}\n\n" if LAST_REPLY else ""
    prompt = context + "You are an expert programming tutor. Be clear and concise.\n\n" + question
    error = None
    for model in (model_for("coder", CODER_MODEL), FALLBACK_MODEL):
        try:
            r = requests.post(OLLAMA_URL, json={"model": model, "prompt": prompt, "stream": False},
                              timeout=300)
            if r.status_code == 404:      # model not downloaded, try the fallback
                continue
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except Exception as e:
            error = e
    return f"The coding model failed: {error or 'model not found (run ollama pull qwen2.5-coder:7b)'}"


# ---------------- Multi-step setups (v4.5) ----------------
SETUPS_FILE = WORKSPACE / "setups.json"
DEFAULT_SETUPS = {
    "coding": [{"type": "app", "value": "vscode"},
               {"type": "open", "value": "amvi project"},
               {"type": "url", "value": "https://www.w3schools.com"}],
    "study": [{"type": "open", "value": "study notes"},
              {"type": "url", "value": "https://www.google.com"},
              {"type": "timer", "value": 30}],
}


def _setups():
    if not SETUPS_FILE.exists():
        SETUPS_FILE.write_text(json.dumps(DEFAULT_SETUPS, indent=2))
    return json.loads(SETUPS_FILE.read_text())


@tool("Run a multi-step setup, e.g. 'coding' or 'study' (start my coding setup)", '{"name": str}')
def run_setup(name):
    setups = _setups()
    key = name.lower().replace("setup", "").replace("my", "").strip()
    steps = setups.get(key)
    if not steps:
        return f"No setup '{name}'. Available: {', '.join(setups)}"
    log = []
    for s in steps:
        try:
            t, v = s["type"], s["value"]
            if t == "app":     log.append(open_app(v))
            elif t == "open":  log.append(open_saved(v))
            elif t == "url":   log.append(open_website(v))
            elif t == "timer": log.append(set_timer(v, f"{key} timer"))
        except Exception as e:
            log.append(f"Skipped {s}: {e}")
        time.sleep(1)
    return "\n".join(log)


# ---------------- Deadlines (v4.5) ----------------
DEADLINES_FILE = WORKSPACE / "deadlines.json"


def _deadlines():
    return json.loads(DEADLINES_FILE.read_text()) if DEADLINES_FILE.exists() else []


@tool("Add an assignment or project deadline", '{"task": str, "date": "YYYY-MM-DD"}')
def add_deadline(task, date):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return "Please give the date as YYYY-MM-DD."
    d = _deadlines(); d.append({"task": task, "date": date})
    DEADLINES_FILE.write_text(json.dumps(d, indent=2))
    return f"Added: {task} due {date}."


@tool("Show upcoming deadlines")
def show_deadlines():
    d = sorted(_deadlines(), key=lambda x: x["date"])
    if not d: return "No deadlines saved."
    today = datetime.now().date()
    return "\n".join(
        f'{x["date"]}  ({(datetime.strptime(x["date"], "%Y-%m-%d").date() - today).days} days)  {x["task"]}'
        for x in d)


# ---------------- Screen reading (v6.0) ----------------
_ocr_engine = None


def _ocr(image):
    """Run local OCR (rapidocr) on a PIL image or an image file path."""
    global _ocr_engine
    import numpy as np
    from PIL import Image
    if _ocr_engine is None:
        print("Amvi: Loading OCR engine...")
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    if isinstance(image, (str, Path)):
        image = Image.open(image).convert("RGB")
    result, _ = _ocr_engine(np.array(image))
    return "\n".join(line[1] for line in result) if result else ""


def _capture_text(delay):
    from PIL import ImageGrab
    delay = max(0, min(int(delay), 15))
    if delay:
        print(f"Amvi: Switch to the window you want me to read... capturing in {delay}s")
        time.sleep(delay)
    return _ocr(ImageGrab.grab())


@tool("Read the text currently visible on the screen (OCR)", '{"delay": number}')
def read_screen(delay=4):
    text = _capture_text(delay)
    return text[:3000] if text else "I couldn't find any text on the screen."


@tool("Explain an error or problem visible on screen and suggest a fix",
      '{"question": str, "delay": number}')
def explain_screen(question="", delay=4):
    text = _capture_text(delay)
    if not text:
        return "I couldn't find any text on the screen."
    ask = question or "Explain any error or problem shown here and how to fix it."
    return ask_coder(f"This text was read from my screen by OCR (it may have small mistakes):\n"
                     f"{text[:4000]}\n\n{ask}")


@tool("Read the text inside an image file", '{"path": str}')
def read_image(path):
    p = _path(path)
    if not p.exists():
        return f"File not found: {p}"
    text = _ocr(p)
    return text[:3000] if text else "I couldn't find any text in that image."


# ---------------- Model settings (v10) ----------------
SETTINGS_FILE = WORKSPACE / "settings.json"


def model_for(role, default):
    """Which Ollama model to use for a role (router, coder, study, vision, agent, embed)."""
    try:
        return json.loads(SETTINGS_FILE.read_text()).get("models", {}).get(role, default)
    except Exception:
        return default


# ---------------- Personality (v12) ----------------
PERSONALITIES = {
    "friendly": "warm, upbeat and encouraging",
    "professional": "polite, precise and businesslike",
    "funny": "witty with light humour, but still helpful and brief",
    "concise": "as brief as possible: one or two short sentences",
    "mentor": "a patient teacher: explain step by step with a small example",
}


def personality_text():
    try:
        key = json.loads(SETTINGS_FILE.read_text()).get("personality", "friendly")
    except Exception:
        key = "friendly"
    return PERSONALITIES.get(key, PERSONALITIES["friendly"])
