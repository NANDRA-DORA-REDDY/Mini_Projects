import difflib
import inspect
import json
import logging
import re
import time

import requests

import amvi_tools as tools
from amvi_tools import TOOLS
import amvi_more_tools as more_tools  # noqa: F401  (registers the extra tools)


# ==========================================
# Amvi CONFIGURATION
# ==========================================

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:latest"          # fast router / chat model
HISTORY_TURNS = 5                  # how many past exchanges Amvi remembers

CONFIRM = input                     # the GUI replaces this with a pop-up

logging.basicConfig(filename="amvi.log", level=logging.INFO,
                    format="%(asctime)s %(message)s", encoding="utf-8")


# ==========================================
# OLLAMA
# ==========================================

def build_tool_list():
    lines = ["chat: normal conversation or a simple question (put the answer in task)"]
    for name, t in TOOLS.items():
        lines.append(f'{name}: {t["desc"][:70]}' + (f' args={t["args"]}' if t["args"] else ""))
    return "\n".join(lines)


def build_history(history):
    if not history:
        return "(none)"
    return "\n".join(f"User: {u}\nAmvi: {r}" for u, r in history[-HISTORY_TURNS:])


def ask_llama(command, history):

    prompt = f"""
You are Amvi, a personal Windows AI assistant.
When you answer in the "task" field, speak in this style: {tools.personality_text()}

Convert the user's command into ONE JSON object.

Allowed actions (never invent others):

{build_tool_list()}

Return ONLY JSON.

The JSON MUST have exactly these fields:

{{
  "action": "one_allowed_action",
  "query": "text",
  "task": "text",
  "args": {{}}
}}

Rules:

1. Google request: action = open_google

2. YouTube request, including:
   "play a song on youtube"
   "play any video"
   "watch a YouTube video"
   action = open_youtube
   (if the user names what to play, use search_site with site youtube)

3. W3Schools request: action = open_w3schools

4. HackerRank request: action = open_hackerrank

5. Search request:
   action = search_google
   query = what the user wants to search for

6. Simple conversation or short factual questions:
   action = chat
   task = your complete answer

7. Writing, explaining, debugging or improving CODE, or any programming
   question:
   action = ask_coder
   args = {{"question": "the user's full request"}}

8. "start my coding setup" / "start my study setup":
   action = run_setup, args = {{"name": "coding"}} or {{"name": "study"}}

8b. "remember that X is at PATH":
   action = remember, args = {{"key": "X", "value": "PATH"}}

9. For any other tool, fill "args" with exactly the argument names
   shown for that tool.

10. Follow-ups like "explain that again" or "make it shorter" refer to the
    recent conversation below.

11. Never put unescaped quotation marks inside JSON strings.

12. Always return valid JSON.

Known facts about the user:
{more_tools.facts_text()}

Recent conversation:
{build_history(history)}

User command:
{command}
"""

    try:

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": tools.model_for("router", MODEL),
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"num_ctx": 8192},
                "keep_alive": "30m"
            },
            timeout=120
        )

        response.raise_for_status()

        text = response.json().get("response", "").strip()

        return json.loads(text)

    except Exception as e:

        print("Amvi: I could not understand that command.")
        print("Amvi error:", e)

        return {
            "action": "chat",
            "query": "",
            "task": "The AI did not answer in time (Ollama may still be loading a model). Try again in a moment, or use an exact command like 'show my deadlines'.",
            "args": {}
        }


# ==========================================
# COMMAND EXECUTION  (returns the reply text)
# ==========================================

# ==========================================
# ACTION NAME CLEAN-UP (the small AI sometimes writes names slightly wrong)
# ==========================================

ACTION_ALIASES = {
    "open_chrome": ("open_app", {"app": "chrome"}), "launch_chrome": ("open_app", {"app": "chrome"}),
    "launch_app": ("open_app", {}), "start_app": ("open_app", {}), "open_application": ("open_app", {}),
    "launch": ("open_app", {}), "run_app": ("open_app", {}),
    "search": ("search_google", {}), "google_search": ("search_google", {}), "web_search": ("search_google", {}),
    "take_screenshot": ("screenshot", {}), "capture_screenshot": ("screenshot", {}),
    "system_info": ("system_status", {}), "get_system_status": ("system_status", {}),
    "shutdown": ("shutdown_computer", {}), "sleep": ("sleep_computer", {}), "lock": ("lock_computer", {}),
    "respond": ("chat", {}), "answer": ("chat", {}), "talk": ("chat", {}), "conversation": ("chat", {}),
    "chat_response": ("chat", {}), "reply": ("chat", {}),
}


def resolve_action(action):
    """Return (real_action_name_or_original, extra_default_args)."""
    key = re.sub(r"[\s\-]+", "_", str(action).strip().lower())
    names = list(TOOLS) + ["chat"]
    if key in names:
        return key, {}
    if key in ACTION_ALIASES:
        name, extra = ACTION_ALIASES[key]
        return name, dict(extra)
    squashed = key.replace("_", "")
    for n in names:
        if n.replace("_", "") == squashed:
            return n, {}
    close = difflib.get_close_matches(key, names, n=1, cutoff=0.8)
    return (close[0], {}) if close else (action, {})


def execute_command(data):

    action = data.get("action", "")
    query = data.get("query", "")
    task = data.get("task", "")
    args = data.get("args") or {}

    if not isinstance(args, dict):
        args = {}

    original = action
    action, extra = resolve_action(action)
    for k, v in extra.items():
        args.setdefault(k, v)
    if action != original and action in TOOLS or action == "chat" and original != "chat":
        logging.info("RESOLVED action %r -> %r", original, action)

    print()
    print("Amvi interpretation:")
    print("  Action:", action)

    if args:
        print("  Args:", str(args)[:120])

    print()

    # --- chat ---
    if action == "chat":

        reply = task or query or "I'm here."
        print("Amvi:", reply)
        return reply

    # --- tool lookup ---
    tool = TOOLS.get(action)

    if tool is None:

        print(f"Amvi: I don't know the action '{action}'. Type help to see what I can do.")
        logging.info("BLOCKED unknown action: %s", action)
        return f"I don't know the action '{action}'."

    # --- safety layer ---
    if tool["destructive"] and not tools.ALLOW_DESTRUCTIVE:

        print("Amvi: Destructive actions are disabled.")
        print("        Set ALLOW_DESTRUCTIVE = True in tools.py to enable.")
        return "Destructive action blocked."

    if tool["dangerous"]:

        answer = CONFIRM(f"Amvi: '{action}' is a risky action. Confirm? (yes/no): ")

        if answer.strip().lower() != "yes":

            print("Amvi: Cancelled.")
            logging.info("DENIED %s %s", action, args)
            return "Cancelled."

    params = inspect.signature(tool["fn"]).parameters

    if "query" in params and "query" not in args and query:
        args["query"] = query

    args = {k: v for k, v in args.items() if k in params}

    # --- run it ---
    try:

        logging.info("RUN %s %s", action, args)

        if action == "ask_coder":
            print("Amvi: Asking the coding model (first use can take a while)...")

        result = str(tool["fn"](**args))

        print("Amvi:", result)
        return result

    except TypeError as e:

        print("Amvi: I'm missing some information for that.")
        print("Amvi error:", e)
        return "Missing information."

    except Exception as e:

        logging.exception("Tool failed")
        print("Amvi: That action failed.")
        print("Amvi error:", e)
        return "Action failed."


# ==========================================
# MULTI-STEP COMMANDS ("open chrome and search for python")
# ==========================================

STEP_VERBS = ("open|search|play|take|read|start|close|restart|find|create|run|show|commit|push|pull|"
              "describe|explain|summarize|summarise|set|add|remember|lock|launch|go|write|make|list")


def split_commands(command):
    # split on "and", "then", "and then" or a comma, but only when a new
    # command verb follows, so "make mcqs on DBMS and OS" stays in one piece
    if re.match(r"^\s*(agent|ask (?:my )?(?:docs|documents|notes))\b", command, re.I):
        return [command]

    pattern = r"\s*(?:,|;|\band then\b|\bthen\b|\band\b)\s+(?=(?:" + STEP_VERBS + r")\b)"
    parts = [p.strip() for p in re.split(pattern, command, flags=re.I) if p.strip()]
    return parts or [command]


# ==========================================
# FAST ROUTES (exact patterns skip the AI)
# ==========================================

def quick_route(command):

    text = command.strip()

    # "remember that my amvi project is at D:\Jarvis"
    m = re.match(r"^remember(?: that)?\s+(?:my\s+)?(.+?)\s+(?:is|are)\s+(?:at|in|on|located at)\s+([A-Za-z]:[\\/].*|~.*|%.*|/.*)$",
                 text, re.I)

    if m:
        return {"action": "remember", "query": "", "task": "",
                "args": {"key": m.group(1).strip(), "value": m.group(2).strip().strip('"\'')}}

    # "open my amvi project" (only if you saved that name)
    m = re.match(r"^open\s+(?:my\s+)?(.+)$", text, re.I)

    if m and m.group(1).strip().lower() in tools._mem():
        return {"action": "open_saved", "query": "", "task": "",
                "args": {"key": m.group(1).strip()}}

    # "open <any app or game Amvi knows>"
    if m and tools._app(m.group(1).strip()):
        return {"action": "open_app", "query": "", "task": "",
                "args": {"app": m.group(1).strip()}}

    # ----- v7 routes -----
    low = text.lower().strip("?. ")
    simple = {"git status": "git_status", "git log": "git_log", "git pull": "git_pull",
              "pull latest changes": "git_pull", "git push": "git_push", "push this project": "git_push",
              "push my project": "git_push", "push to github": "git_push",
              "list apps": "list_apps", "study report": "study_report",
              "help": "show_help", "commands": "show_help", "what can you do": "show_help",
              "what can i say": "show_help", "show commands": "show_help",
              "show reminders": "show_reminders", "my reminders": "show_reminders",
              "list reminders": "show_reminders", "clear reminders": "clear_reminders"}

    if low in simple:
        return {"action": simple[low], "query": "", "task": "", "args": {}}

    # ----- fast pack: common commands that never need the AI router -----
    sites_open = {"youtube": "open_youtube", "google": "open_google", "w3schools": "open_w3schools",
                  "hackerrank": "open_hackerrank"}
    m = re.match(r"^(?:open|go to)\s+(youtube|google|w3schools|hackerrank)(?:\.com)?$", low)
    if m:
        return {"action": sites_open[m.group(1)], "query": "", "task": "", "args": {}}

    m = re.match(r"^(?:open|go to)\s+(github|stack ?overflow)(?:\.com)?$", low)
    if m:
        return {"action": "open_website", "query": "", "task": "",
                "args": {"url": m.group(1).replace(" ", "") + ".com"}}

    m = re.match(r"^search\s+(github|youtube|stack ?overflow|w3schools|hackerrank|google)\s+for\s+(.+)$", text, re.I)
    if m:
        return {"action": "search_site", "query": "", "task": "",
                "args": {"site": m.group(1).lower().replace(" ", ""), "query": m.group(2).strip()}}

    if re.match(r"^(?:show|check|view|list)?\s*(?:my\s+)?(?:upcoming\s+)?deadlines$", low):
        return {"action": "show_deadlines", "query": "", "task": "", "args": {}}

    m = re.match(r"^add\s+(?:a\s+)?deadline\s*:?\s*(.+?)\s+(?:on|by|due)\s+(\d{4}-\d{2}-\d{2})$", text, re.I)
    if m:
        return {"action": "add_deadline", "query": "", "task": "",
                "args": {"task": m.group(1).strip(), "date": m.group(2)}}

    if re.match(r"^(?:how much|what(?:'s| is)|show|check)\b.*\b(?:ram|memory|cpu|battery)\b", low):
        return {"action": "system_status", "query": "", "task": "", "args": {}}

    if re.match(r"^(?:battery|cpu|ram|memory|system)(?:\s+(?:status|usage|info))?$", low):
        return {"action": "system_status", "query": "", "task": "", "args": {}}

    if re.match(r"^(?:what(?:'s| is)|show|check)\b.*\bgpu\b", low):
        return {"action": "gpu_status", "query": "", "task": "", "args": {}}

    if re.search(r"\b(?:disk|storage|free) space\b", low):
        return {"action": "disk_space", "query": "", "task": "", "args": {}}

    if low in ("lock", "lock the computer", "lock computer", "lock my pc", "lock the pc"):
        return {"action": "lock_computer", "query": "", "task": "", "args": {}}

    m = re.match(r"^add\s+(?:a\s+)?note\s*:?\s*(.+)$", text, re.I)
    if m:
        return {"action": "add_note", "query": "", "task": "", "args": {"text": m.group(1).strip()}}

    m = re.match(r"^(?:start|set)\s+(?:a\s+)?(\d+(?:\.\d+)?)[\s-]*(?:minutes?|mins?)\s+(?:\w+\s+)?timer$", low)
    if m:
        return {"action": "set_timer", "query": "", "task": "", "args": {"minutes": float(m.group(1))}}

    m = re.match(r"^create\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?(.+)$", text, re.I)
    if m:
        return {"action": "create_folder", "query": "", "task": "",
                "args": {"path": m.group(1).strip().strip('"\'')}}

    ext = {"python": "*.py", "pdf": "*.pdf", "text": "*.txt", "word": "*.docx", "excel": "*.xlsx",
           "powerpoint": "*.pptx", "c++": "*.cpp", "cpp": "*.cpp", "java": "*.java", "javascript": "*.js",
           "html": "*.html", "csv": "*.csv"}
    m = re.match(r"^find\s+(?:all\s+)?(?:my\s+)?(python|pdf|text|word|excel|powerpoint|c\+\+|cpp|java|javascript|html|csv)"
                 r"\s+files?$", low)
    if m:
        return {"action": "find_files", "query": "", "task": "", "args": {"pattern": ext[m.group(1)]}}

    m = re.match(r"^(close|restart)\s+(?:the\s+)?(.+)$", text, re.I)
    if m and tools._app(m.group(2).strip()):
        return {"action": m.group(1).lower() + "_app", "query": "", "task": "",
                "args": {"app": m.group(2).strip()}}

    wm = {"minimize window": "minimize", "minimise window": "minimize", "maximize window": "maximize",
          "maximise window": "maximize", "restore window": "restore", "switch window": "switch",
          "switch windows": "switch", "snap left": "snap_left", "snap right": "snap_right",
          "show desktop": "show_desktop"}
    if low in wm:
        return {"action": "window_action", "query": "", "task": "", "args": {"action": wm[low]}}

    if low in ("close window", "close this window", "close the window", "close active window"):
        return {"action": "close_window", "query": "", "task": "", "args": {}}

    if low in ("read clipboard", "what's in my clipboard", "what is in my clipboard", "show clipboard"):
        return {"action": "read_clipboard", "query": "", "task": "", "args": {}}

    m = re.match(r"^(explain|summari[sz]e|fix|debug|review|translate)\s+(?:the\s+|my\s+)?"
                 r"(?:clipboard|copied (?:text|code|error)|what i copied)\b(.*)$", text, re.I)
    if m:
        return {"action": "ask_clipboard", "query": "", "task": "",
                "args": {"request": (m.group(1) + " this " + m.group(2)).strip()}}

    m = re.match(r"^analy[sz]e\s+(?:my\s+|the\s+)?(?:data\s+|file\s+|dataset\s+)?(.+\.(?:csv|xlsx|xls))$", text, re.I)
    if m:
        return {"action": "analyze_csv", "query": "", "task": "", "args": {"path": m.group(1).strip().strip('"\'')}}

    if low in ("good morning", "good afternoon", "good evening", "daily briefing", "morning briefing", "brief me",
               "what's my day look like", "what is my day like"):
        return {"action": "daily_briefing", "query": "", "task": "", "args": {}}

    startup = {"start with windows": "on", "start amvi with windows": "on", "enable startup": "on",
               "run at startup": "on", "don't start with windows": "off", "do not start with windows": "off",
               "stop starting with windows": "off", "disable startup": "off", "startup status": "status"}
    if low in startup:
        return {"action": "startup_launch", "query": "", "task": "", "args": {"mode": startup[low]}}

    m = re.match(r"^personality\s*:?\s*(\w+)?$", low)
    if m:
        return {"action": "set_personality", "query": "", "task": "", "args": {"style": m.group(1) or ""}}

    m = re.match(r"^remind me\s+(?:to\s+|about\s+)?(.+?)\s+((?:tomorrow\b.*|at\s+\d.*|in\s+\d.*))$", text, re.I)
    if m:
        return {"action": "remind_me", "query": "", "task": "",
                "args": {"text": m.group(1).strip(), "when": m.group(2).strip()}}

    m = re.match(r"^agent\s*:?\s+(.+)$", text, re.I)
    if m:
        return {"action": "run_agent", "query": "", "task": "", "args": {"goal": m.group(1).strip()}}

    simple2 = {"show facts": "show_facts", "what do you know about me": "show_facts",
               "what do you remember": "show_facts", "index my documents": "index_docs",
               "index my study folder": "index_docs", "index documents": "index_docs",
               "show models": "show_models", "list models": "show_models",
               "take a screenshot": "screenshot", "take screenshot": "screenshot",
               "screenshot": "screenshot", "capture screen": "screenshot"}

    if low in simple2:
        return {"action": simple2[low], "query": "", "task": "", "args": {}}

    m = re.match(r"^(?:ask (?:my )?(?:docs|documents|notes)|search my (?:docs|documents|notes) for)\s*:?\s*(.+)$",
                 text, re.I)
    if m:
        return {"action": "ask_docs", "query": "", "task": "", "args": {"question": m.group(1).strip()}}

    m = re.match(r"^(?:set|use)\s+(?:the\s+)?(router|coder|study|vision|agent|embed)\s+model\s+(?:to\s+)?(\S+)$",
                 text, re.I)
    if m:
        return {"action": "set_model", "query": "", "task": "",
                "args": {"role": m.group(1), "name": m.group(2)}}

    m = re.match(r"^forget\s+(?:about\s+)?(.+)$", text, re.I)
    if m:
        return {"action": "forget_fact", "query": "", "task": "", "args": {"keyword": m.group(1).strip()}}

    m = re.match(r"^remember\s+(?:that\s+)?(.+)$", text, re.I)      # facts (paths are handled earlier)
    if m:
        return {"action": "remember_fact", "query": "", "task": "", "args": {"fact": m.group(1).strip()}}

    if re.match(r"^(?:open\s+(?:an?\s+)?apps?(?:\s+menu)?|apps?\s+menu|available apps)$", low):
        return {"action": "app_menu", "query": "", "task": "", "args": {}}

    if re.match(r"^(?:web\s+search|search\s+(?:the\s+)?web|search\s+from\s+web|search\s+web)$", low):
        return {"action": "web_menu", "query": "", "task": "", "args": {}}

    if re.match(r"^(?:start\s+|open\s+)?(?:my\s+)?cod(?:e|ing)\s+(?:setup|menu)$", low):
        return {"action": "coding_menu", "query": "", "task": "", "args": {}}

    if low in ("full coding setup", "start full coding setup", "start my full coding setup"):
        return {"action": "run_setup", "query": "", "task": "", "args": {"name": "coding"}}

    media = {"pause": "playpause", "resume": "playpause", "play": "playpause", "play pause": "playpause",
             "next": "nexttrack", "next song": "nexttrack", "next track": "nexttrack",
             "previous": "prevtrack", "previous song": "prevtrack", "previous track": "prevtrack",
             "volume up": "volumeup", "volume down": "volumedown", "mute": "volumemute",
             "unmute": "volumemute"}

    if low in media:
        return {"action": "media_key", "query": "", "task": "", "args": {"key": media[low]}}

    m = re.match(r"^play\s+(.+?)(?:\s+(?:on|in|from)\s+youtube)?$", text, re.I)
    if m and "spotify" not in text.lower():
        return {"action": "play_youtube", "query": "", "task": "", "args": {"query": m.group(1).strip()}}

    m = re.match(r"^(?:add|register|teach)\s+(?:app|game)\s+(.+?)\s+(?:at|path|is at)\s+(.+)$", text, re.I)
    if m:
        return {"action": "add_app", "query": "", "task": "",
                "args": {"name": m.group(1).strip(), "path": m.group(2).strip().strip('"\'')}}

    m = re.match(r"^(?:write|create|make|generate)\s+(?:a |an )?(?:(python|c\+\+|cpp|c|javascript|js|java|html)\s+)?"
                 r"(?:program|code|script)\s+(?:to|for|that|which)\s+(.+)$", text, re.I)
    if m:
        return {"action": "write_code", "query": "", "task": "",
                "args": {"description": m.group(2).strip(), "language": m.group(1) or "python"}}

    m = re.match(r"^run\s+(?:my\s+)?(?:program\s+|file\s+|code\s+)?(\S+\.(?:py|c|cpp|js))$", text, re.I)
    if m:
        return {"action": "run_code", "query": "", "task": "", "args": {"path": m.group(1)}}

    m = re.match(r"^(?:review|debug|check)\s+(?:my\s+)?(?:code\s+|file\s+)?(\S+\.(?:py|c|cpp|js|java|html))$", text, re.I)
    if m:
        return {"action": "review_code", "query": "", "task": "", "args": {"path": m.group(1)}}

    m = re.match(r"^create\s+(?:a\s+|an\s+)?(python|web|html|c\+\+|cpp|c)\s+project\s+(?:called|named)\s+(.+)$", text, re.I)
    if m:
        return {"action": "create_project", "query": "", "task": "",
                "args": {"name": m.group(2).strip(), "kind": m.group(1)}}

    m = re.match(r"^summari[sz]e\s+(?:this\s+)?(?:page\s+|url\s+|link\s+|website\s+)?(https?://\S+)$", text, re.I)
    if m:
        return {"action": "summarize_url", "query": "", "task": "", "args": {"url": m.group(1)}}

    m = re.match(r"^(?:summarize|summarise)\s+(.+)$", text, re.I)
    if m:
        return {"action": "study", "query": "", "task": "",
                "args": {"task": "summary", "source": m.group(1).strip().strip('"\'')}}

    m = re.match(r"^(?:make|create|generate)\s+(?:\d+\s+)?(mcqs?|notes|2[\s-]?marks?|4[\s-]?marks?|8[\s-]?marks?)"
                 r"(?:\s+(?:questions|answers))?\s+(?:from|on|about|for)\s+(.+)$", text, re.I)
    if m:
        return {"action": "study", "query": "", "task": "",
                "args": {"task": m.group(1), "source": m.group(2).strip().strip('"\'')}}

    m = re.match(r"^quiz me\s+(?:on|about)\s+(.+)$", text, re.I)
    if m:
        return {"action": "quiz_me", "query": "", "task": "", "args": {"topic": m.group(1).strip()}}

    m = re.match(r"^(?:git )?commit(?: my changes)?(?:\s*(?:with message|:)\s*(.+))?$", text, re.I)
    if m:
        return {"action": "git_commit", "query": "", "task": "",
                "args": {"message": m.group(1).strip()} if m.group(1) else {}}

    m = re.match(r"^(?:git )?(?:switch|checkout|create)\s+(?:to\s+)?branch\s+(\S+)$", text, re.I)
    if m:
        return {"action": "git_branch", "query": "", "task": "", "args": {"name": m.group(1)}}

    m = re.match(r"^(?:git )?clone\s+(https://\S+)$", text, re.I)
    if m:
        return {"action": "git_clone", "query": "", "task": "", "args": {"url": m.group(1)}}

    m = re.match(r"^describe\s+(?:the\s+)?(?:image|picture)\s+(.+)$", text, re.I)
    if m:
        return {"action": "describe_image", "query": "", "task": "", "args": {"path": m.group(1).strip().strip('"\'')}}

    if re.search(r"\b(describe|look at)\b.*\bscreen\b", text, re.I):
        return {"action": "describe_screen", "query": "", "task": "", "args": {"question": text}}

    m = re.match(r"^(?:search|google)\s+(?:for\s+)?(.+)$", text, re.I)      # plain "search for python"
    if m:
        return {"action": "search_google", "query": "", "task": "", "args": {"query": m.group(1).strip()}}

    # "read image screenshot_123.png"
    m = re.match(r"^read\s+(?:the\s+)?(?:image|picture|file)\s+(.+)$", text, re.I)

    if m:
        return {"action": "read_image", "query": "", "task": "",
                "args": {"path": m.group(1).strip().strip('"\'')}}

    # screen reading: "read my screen" / "explain the error on my screen"
    if re.search(r"\b(explain|fix|debug|solve|help with)\b.*\bscreen\b", text, re.I):
        return {"action": "explain_screen", "query": "", "task": "", "args": {"question": text}}

    if re.search(r"\b(read|what'?s on|what is on)\b.*\bscreen\b", text, re.I):
        return {"action": "read_screen", "query": "", "task": "", "args": {}}

    return None


def main():
    # ==========================================
    # START
    # ==========================================

    print("================================")
    print("       Amvi v12.0")
    print("================================")
    print()

    print("Amvi: Online.")
    print(f"Amvi: {len(TOOLS)} tools loaded. Conversation memory on.")
    print()
    print("v12.0: good morning (briefing) | personality funny | start with windows | voice wake add <word>")
    print()
    print("v11.0: hands-free voice (GUI) | minimize window | explain clipboard | analyze data.csv | summarize https://...")
    print()
    print("v10.0: agent: open vs code and create a python project called Shop | remember that my exam is on Friday")
    print("       index my documents | ask my notes: what is normalization | show models | use coder model NAME")
    print()
    print("v7.0: write a python program to sort numbers | run <file.py> | review <file.py>")
    print("      create a python project called Shop | summarize notes.pdf | make mcqs on DBMS")
    print("      make 4 mark answers from unit1.pdf | quiz me on recursion | describe my screen")
    print("      git status | commit my changes | push this project | add app steam at C:\\path\\steam.exe")
    print()
    print("Screen (v6.0): read my screen | explain the error on my screen | read image notes.png")
    print()
    print("Long questions: type  paste , paste the whole question, then type END")
    print()
    print("Voice: type  listen  to talk | voice on / off | voice speed 170 | voice list | voice neerja | voice local")
    print()
    print("New in v4.5:")
    print()
    print("  write a python function to reverse a string")
    print("  make it shorter            (follow-up works now)")
    print("  start my coding setup")
    print("  start my study setup")
    print("  add deadline: DBMS record on 2026-10-20")
    print("  show my deadlines")
    print()
    print("Type 'exit' to stop.")
    print()


    # ==========================================
    # MAIN LOOP
    # ==========================================

    history = more_tools.load_history()
    speak_on = False

    try:
        import amvi_voice as voice
    except Exception as e:
        voice = None
        print("Amvi: Voice not available:", e)
        print()

    while True:

        command = input("YOU: ").strip()

        if not command:
            continue

        low = command.lower()
        from_voice = False
        forced = None
        from_paste = False

        if low in ["exit", "quit", "stop"]:

            print()
            print("Amvi: Shutting down.")

            break

        # ---- multi-line paste mode ----
        if low in ["paste", "multi", "multiline"]:

            print("Amvi: Paste your full question. Type END on its own line when finished.")

            lines = []

            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip().upper() == "END":
                    break
                lines.append(line)

            command = "\n".join(lines).strip()

            if not command:
                print("Amvi: Nothing received.")
                print()
                continue

            hint = " Use MySQL syntax." if "sql" in command.lower() else ""

            forced = {"action": "ask_coder", "query": "", "task": "",
                      "args": {"question": "Answer every part of the following question completely and in "
                                           "order, with a working example for each part." + hint +
                                           "\n\n" + command}}
            from_paste = True

        # ---- voice commands ----
        if low == "listen" or low.startswith("voice"):

            if voice is None:
                print("Amvi: Voice is not installed. Run: pip install -r requirements.txt")
                print()
                continue

            if low == "voice on":
                speak_on = True
                print("Amvi: Voice replies on.")
                voice.speak("Voice replies enabled.")

            elif low == "voice off":
                speak_on = False
                print("Amvi: Voice replies off.")

            elif low == "voice list":
                print(voice.list_voices())

            elif low.startswith("voice speed"):
                try:
                    voice.set_speed(int(low.split()[-1]))
                    print("Amvi: Speaking speed updated.")
                except ValueError:
                    print("Amvi: Use a number, e.g. voice speed 170")

            elif low.startswith("voice ") and low != "voice":
                ok, msg = voice.choose(command[5:].strip())
                print("Amvi:", msg)
                if ok:
                    voice.speak("Hello, this is my new voice.")

            elif low == "listen":
                print("Amvi: Listening... speak now.")

                try:
                    command = voice.listen()
                except Exception as e:
                    print("Amvi: Microphone problem:", e)
                    print()
                    continue

                if not command:
                    print("Amvi: I didn't hear anything.")
                    print()
                    continue

                print("YOU (voice):", command)
                from_voice = True

            else:
                print("Amvi: Try: listen | voice on | voice off | voice speed 170 | voice list | voice neerja | voice local")

            if not from_voice:
                print()
                continue

        print()
        print("Amvi: Thinking...")

        parts = [command] if (forced or from_paste) else split_commands(command)

        if len(parts) > 1:
            print("Amvi: Doing " + str(len(parts)) + " steps: " + " -> ".join(parts))

        replies = []

        for part in parts:
            data = forced or quick_route(part) or ask_llama(part, history)
            replies.append(execute_command(data))
            if len(parts) > 1:
                time.sleep(1.5)

        reply = "\n".join(replies)

        if from_paste:
            out_dir = tools.WORKSPACE / "answers"
            out_dir.mkdir(exist_ok=True)
            saved = out_dir / f"answer_{int(time.time())}.txt"
            saved.write_text("QUESTION:\n" + command + "\n\nANSWER:\n" + reply, encoding="utf-8")
            print(f"Amvi: Saved to {saved}")

        history.append((command[:200], reply[:400]))
        more_tools.save_history(history)
        tools.LAST_REPLY = reply

        if voice is not None and (speak_on or from_voice):
            voice.speak(reply)

        print()


if __name__ == "__main__":
    main()
