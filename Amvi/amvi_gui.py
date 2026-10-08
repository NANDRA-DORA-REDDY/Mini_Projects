"""amvi_gui.py - Amvi v9.0 dark GUI (PySide6). Run: python amvi_gui.py
It reuses the same engine as the terminal version (jarvis.py, tools.py, more_tools.py, voice.py)."""
import os
import sys

# pythonw.exe has no console, so stdout/stderr can be None: give them somewhere to go
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import html
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque

import psutil
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget, QMenu, QSystemTrayIcon)

import amvi as jarvis          # the engine (kept the short name inside this file)
import amvi_more_tools as more_tools
import amvi_tools as tools

try:
    import amvi_voice as voice
    VOICE_ERR = ""
except Exception as e:           # voice is optional
    voice, VOICE_ERR = None, str(e)

STYLE = """
* { font-family: 'Segoe UI'; color: #cfe8ff; }
QMainWindow, QWidget#root { background: #070b14; }
QDialog { background: #0d1424; }
QLineEdit { background: #0a101d; border: 1px solid #22406b; border-radius: 6px; padding: 6px; }
QFrame#side, QFrame#card { background: #0d1424; border: 1px solid #1a2a45; border-radius: 12px; }
QLabel#title { font-size: 22px; font-weight: 700; color: #00d4ff; letter-spacing: 4px; }
QLabel#sub { color: #5d7a9c; font-size: 11px; }
QTextBrowser, QPlainTextEdit { background: #0a101d; border: 1px solid #1a2a45; border-radius: 10px;
                               padding: 8px; font-size: 13px; selection-background-color: #00d4ff; selection-color: #000; }
QPlainTextEdit#log { font-family: Consolas; font-size: 11px; color: #6f8cab; }
QPushButton { background: #112038; border: 1px solid #22406b; border-radius: 8px; padding: 7px 12px; }
QPushButton:hover { background: #17305a; border-color: #00d4ff; }
QPushButton#send { background: #00a8cc; color: #001018; font-weight: 700; border: none; }
QPushButton#send:hover { background: #00d4ff; }
QPushButton#mic { font-size: 16px; }
QProgressBar { background: #0a101d; border: 1px solid #1a2a45; border-radius: 6px; height: 14px;
               text-align: center; font-size: 10px; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0077aa, stop:1 #00d4ff); border-radius: 5px; }
QCheckBox { spacing: 8px; }
"""


# commands that look at the screen: Amvi minimizes itself first so it is not in the picture
SCREEN_ACTIONS = {"screenshot", "read_screen", "describe_screen", "explain_screen", "window_action", "close_window"}
WAKE_STATUS = "Listening for 'Hey Amvi'..."


class Bridge(QObject):
    """Thread-safe signals: worker threads emit, the GUI thread reacts."""
    log = Signal(str)
    chat = Signal(str, str)
    status = Signal(str)
    confirm = Signal(str)
    ask = Signal(str)
    stats = Signal(float, float, float, str, str)
    menu = Signal(str, list)
    voiceflag = Signal(bool)
    hf_off = Signal()
    pick = Signal(str, list)
    file = Signal(str)
    hide_window = Signal()
    show_window = Signal()


class Writer:
    """Sends print() output from the engine into the activity log."""
    def __init__(self, emit):
        self.emit, self.buf = emit, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.emit(line)
        return len(s)

    def flush(self):
        pass


class InputBox(QPlainTextEdit):
    submitted = Signal()
    nav = Signal(int)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submitted.emit(); return
        if e.key() in (Qt.Key_Up, Qt.Key_Down) and "\n" not in self.toPlainText():
            self.nav.emit(-1 if e.key() == Qt.Key_Up else 1); return
        super().keyPressEvent(e)


def make_icon():
    """Draw the tray icon in code (a cyan disc with an A), so no image file is needed."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#00a8cc"))
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor("#001018"))
    p.setFont(QFont("Segoe UI", 30, QFont.Weight.Bold))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "A")
    p.end()
    return QIcon(pm)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("A.M.V.I")
        self.resize(1150, 780)
        self.history = more_tools.load_history()
        self.cmd_hist, self.hist_pos = [], 0
        self.busy = threading.Lock()
        self.answers = queue.Queue()
        self.recent = deque(maxlen=8)
        self.speak_on = False
        self.handsfree, self.hf_gen = False, 0

        self.bridge = Bridge()
        self.bridge.log.connect(self.on_log)
        self.bridge.chat.connect(self.add_chat)
        self.bridge.status.connect(self.on_status)
        self.bridge.confirm.connect(self.on_confirm)
        self.bridge.ask.connect(self.on_ask)
        self.bridge.stats.connect(self.on_stats)
        self.bridge.menu.connect(self.on_menu)
        self.bridge.pick.connect(self.on_pick)
        self.bridge.file.connect(self.on_file)
        self.bridge.hide_window.connect(self.on_hide)
        self.bridge.show_window.connect(self.on_show)
        self.bridge.voiceflag.connect(lambda v: self.voice_cb.setChecked(v))
        self.bridge.hf_off.connect(lambda: self.hf_cb.setChecked(False))

        self.build_ui()
        self.setup_tray()

        # engine hooks: pop-ups instead of terminal prompts
        jarvis.CONFIRM = self.worker_confirm
        more_tools.CONFIRM = self.worker_confirm
        more_tools.INPUT = self.worker_ask
        more_tools.MENU = self.worker_menu
        more_tools.PICK = self.worker_pick
        more_tools.PICKFILE = self.worker_file
        sys.stdout = sys.stderr = Writer(self.bridge.log.emit)

        threading.Thread(target=self.stats_loop, daemon=True).start()
        self.add_chat("jarvis", f"Online. {len(tools.TOOLS)} tools loaded. Type 'help' to see what I can do.")
        if voice is None:
            self.on_log(f"Voice unavailable: {VOICE_ERR}")

    # ---------------- UI ----------------
    def build_ui(self):
        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        outer = QHBoxLayout(root); outer.setContentsMargins(14, 14, 14, 14); outer.setSpacing(14)

        side = QFrame(); side.setObjectName("side"); side.setFixedWidth(270)
        sl = QVBoxLayout(side); sl.setContentsMargins(16, 16, 16, 16); sl.setSpacing(8)
        t = QLabel("A.M.V.I"); t.setObjectName("title"); sl.addWidget(t)
        s = QLabel("Your personal AI assistant"); s.setObjectName("sub"); sl.addWidget(s)
        self.status = QLabel("\u25cf Online"); self.status.setStyleSheet("color:#2ee59d; font-weight:600;")
        sl.addWidget(self.status); sl.addSpacing(10)

        self.bars = {}
        for name in ("CPU", "RAM", "GPU"):
            lab = QLabel(name); sl.addWidget(lab)
            bar = QProgressBar(); bar.setRange(0, 100); sl.addWidget(bar)
            self.bars[name] = (lab, bar)
        self.extra = QLabel(""); self.extra.setObjectName("sub"); sl.addWidget(self.extra)
        sl.addSpacing(10)

        self.voice_cb = QCheckBox("Voice replies")
        self.voice_cb.toggled.connect(lambda v: setattr(self, "speak_on", v))
        sl.addWidget(self.voice_cb)
        self.hf_cb = QCheckBox("Hands-free: say 'Hey Amvi'")
        self.hf_cb.toggled.connect(self.set_handsfree); sl.addWidget(self.hf_cb)
        for label, cmd in (("Coding setup", "start my coding setup"), ("Open app", "open app"), ("Web search", "web search"),
                           ("Screenshot", "take a screenshot"), ("Deadlines", "show my deadlines"),
                           ("Reminders", "show reminders"), ("Help", "help")):
            b = QPushButton(label); b.clicked.connect(lambda _=False, c=cmd: self.submit(c)); sl.addWidget(b)
        stop = QPushButton("Stop agent"); stop.clicked.connect(more_tools.AGENT_STOP.set); sl.addWidget(stop)
        quiet = QPushButton("Stop speaking"); quiet.clicked.connect(lambda: voice and voice.stop_speaking()); sl.addWidget(quiet)
        sl.addStretch()
        outer.addWidget(side)

        right = QVBoxLayout(); right.setSpacing(10)
        self.chat = QTextBrowser(); self.chat.setOpenExternalLinks(True)
        right.addWidget(self.chat, 5)
        self.logbox = QPlainTextEdit(); self.logbox.setObjectName("log"); self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(500); self.logbox.setPlaceholderText("Activity log")
        right.addWidget(self.logbox, 2)

        row = QHBoxLayout()
        self.input = InputBox(); self.input.setFixedHeight(64)
        self.input.setPlaceholderText("Type a command...  Enter = send, Shift+Enter = new line, \u2191 = history")
        self.input.submitted.connect(self.on_send); self.input.nav.connect(self.on_nav)
        row.addWidget(self.input, 1)
        mic = QPushButton("\U0001F3A4"); mic.setObjectName("mic"); mic.setFixedSize(56, 64)
        mic.clicked.connect(self.on_mic); row.addWidget(mic)
        send = QPushButton("Send"); send.setObjectName("send"); send.setFixedSize(80, 64)
        send.clicked.connect(self.on_send); row.addWidget(send)
        right.addLayout(row)
        outer.addLayout(right, 1)

    # ---------------- slots (GUI thread) ----------------
    def add_chat(self, role, text):
        who, color = ("YOU", "#7aa2c7") if role == "you" else ("Amvi", "#00d4ff")
        body = html.escape(text).replace("\n", "<br>")
        self.chat.append(f'<div style="margin:6px 0; white-space:pre-wrap;">'
                         f'<b style="color:{color}">{who}</b><br>{body}</div>')
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def on_log(self, text):
        if text.startswith("YOU: "):
            text = text[5:]
        self.recent.append(text)
        self.logbox.appendPlainText(text)

    def on_status(self, text):
        color = {"Online": "#2ee59d", "Thinking...": "#ffb347", "Listening...": "#ff5d73",
                 "Speaking...": "#00d4ff", WAKE_STATUS: "#9d7bff"}.get(text, "#2ee59d")
        self.status.setText(f"\u25cf {text}")
        self.status.setStyleSheet(f"color:{color}; font-weight:600;")

    def on_stats(self, cpu, ram, gpu, gpu_txt, bat):
        for name, v in (("CPU", cpu), ("RAM", ram), ("GPU", gpu)):
            lab, bar = self.bars[name]
            bar.setValue(int(max(v, 0)))
            lab.setText(f"{name}  {v:.0f}%" if v >= 0 else f"{name}  n/a")
        self.extra.setText(f"{gpu_txt}   {bat}".strip())

    def on_confirm(self, msg):
        text = msg.replace("Amvi: ", "").replace("Confirm? (yes/no): ", "Confirm?")
        r = QMessageBox.question(self, "Amvi needs permission", text,
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        self.answers.put("yes" if r == QMessageBox.Yes else "no")

    def on_ask(self, prompt):
        context = "\n".join(self.recent) + "\n\n" if "Your answer" in prompt else ""
        text, ok = QInputDialog.getText(self, "Amvi", context + prompt)
        self.answers.put(text if ok else "q")

    def on_menu(self, title, options):
        dlg = QDialog(self); dlg.setWindowTitle(title); dlg.setMinimumWidth(320)
        lay = QVBoxLayout(dlg)
        head = QLabel(title); head.setStyleSheet('font-size:16px; font-weight:600; color:#00d4ff;'); lay.addWidget(head)
        for i, o in enumerate(options, 1):
            b = QPushButton(f'{i}.  {o}'); b.setMinimumHeight(38)
            b.clicked.connect(lambda _=False, n=i: dlg.done(n)); lay.addWidget(b)
        self.answers.put(dlg.exec())          # 0 if the window is closed

    def on_pick(self, title, items):
        dlg = QDialog(self); dlg.setWindowTitle(title); dlg.setMinimumSize(380, 440)
        lay = QVBoxLayout(dlg)
        head = QLabel(title); head.setStyleSheet("font-size:16px; font-weight:600; color:#00d4ff;"); lay.addWidget(head)
        box = QLineEdit(); box.setPlaceholderText("Type an app name, then press Enter..."); lay.addWidget(box)
        lst = QListWidget(); lay.addWidget(lst)
        note = QLabel(""); note.setStyleSheet("color:#ff8a8a;"); lay.addWidget(note)
        chosen = [""]

        def fill():
            q = box.text().strip().lower()
            lst.clear()
            for it in items:
                if q in it.lower():
                    lst.addItem(it)
            if lst.count():
                lst.setCurrentRow(0); note.setText("")
            else:
                note.setText("No matching app")

        def accept():
            item = lst.currentItem()
            if item:
                chosen[0] = item.text(); dlg.accept()

        box.textChanged.connect(fill); box.returnPressed.connect(accept)
        lst.itemDoubleClicked.connect(lambda _item: accept())
        fill(); box.setFocus()
        dlg.exec()
        self.answers.put(chosen[0])

    def on_file(self, title):
        path, _ = QFileDialog.getOpenFileName(self, title, "C:\\Program Files",
                                              "Programs (*.exe *.lnk *.bat);;All files (*)")
        self.answers.put(path or "")

    def on_hide(self):
        self._tray_hidden = self.isHidden()          # already hidden in the tray? then leave it
        if not self._tray_hidden:
            self.showMinimized()

    def on_show(self):
        if getattr(self, "_tray_hidden", False):
            return
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show(); self.raise_(); self.activateWindow()

    def set_handsfree(self, on):
        self.handsfree = on
        if not on:
            self.bridge.status.emit("Online")
            return
        if voice is None:
            self.add_chat("jarvis", f"Voice isn't available: {VOICE_ERR}")
            self.hf_cb.setChecked(False)
            return
        self.hf_gen += 1
        threading.Thread(target=self.handsfree_loop, args=(self.hf_gen,), daemon=True).start()

    def handsfree_loop(self, gen):
        """Wait for 'Hey Amvi', then take the command that follows (same sentence or the next one)."""
        self.init_com()
        self.bridge.chat.emit("jarvis", "Hands-free is on. Say 'Hey Amvi', then your command.")
        while self.handsfree and gen == self.hf_gen:
            if self.busy.locked():
                time.sleep(0.3)
                continue
            try:
                self.bridge.status.emit(WAKE_STATUS)
                heard = voice.listen(max_seconds=6, idle_seconds=3)
            except Exception as e:
                self.bridge.chat.emit("jarvis", f"Microphone problem: {e}")
                self.handsfree = False
                self.bridge.hf_off.emit()
                break
            if heard:
                print(f"[hands-free] heard: {heard}")      # shows in the activity log, handy for tuning
            m = voice.WAKE_RE.search(heard) if heard else None
            if not m or not (self.handsfree and gen == self.hf_gen):
                continue
            command = heard[m.end():].strip(" ,.!?")
            if not self.busy.acquire(blocking=False):
                continue
            if len(command) < 3:                         # only the wake word: ask for the command
                try:
                    self.bridge.status.emit("Listening...")
                    voice.speak("Yes?")
                    command = voice.listen(max_seconds=10, idle_seconds=5)
                except Exception as e:
                    self.bridge.chat.emit("jarvis", f"Microphone problem: {e}")
                    command = ""
            if not command.strip():
                self.bridge.status.emit("Online")
                self.busy.release()
                continue
            self.bridge.chat.emit("you", command)
            self.worker(command, True)                   # releases the busy lock when finished

    def setup_tray(self):
        self.quitting = False
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("Amvi")
        menu = QMenu()
        menu.addAction("Show Amvi").triggered.connect(self.bring_front)
        self.tray_hf = menu.addAction("Hands-free (say 'Hey Amvi')")
        self.tray_hf.setCheckable(True)
        self.tray_hf.toggled.connect(self.hf_cb.setChecked)
        self.hf_cb.toggled.connect(self.tray_hf.setChecked)
        menu.addSeparator()
        menu.addAction("Quit Amvi").triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu                       # keep a reference so it is not garbage-collected
        self.tray.activated.connect(self.on_tray_click)
        self.tray.show()

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.bring_front()

    def bring_front(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show(); self.raise_(); self.activateWindow()

    def closeEvent(self, event):
        if self.tray is not None and not self.quitting:      # the X button hides Amvi to the tray
            event.ignore()
            self.hide()
            self.tray.showMessage("Amvi is still running", "Click the tray icon to open me. Right-click for Quit.",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            event.accept()

    def quit_app(self):
        self.quitting = True
        QApplication.quit()

    def on_send(self):
        text = self.input.toPlainText()
        self.input.clear()
        self.submit(text)

    def on_nav(self, d):
        if not self.cmd_hist:
            return
        self.hist_pos = max(0, min(len(self.cmd_hist), self.hist_pos + d))
        self.input.setPlainText(self.cmd_hist[self.hist_pos] if self.hist_pos < len(self.cmd_hist) else "")

    def on_mic(self):
        if voice is None:
            self.add_chat("jarvis", f"Voice isn't available: {VOICE_ERR}"); return
        if not self.busy.acquire(blocking=False):
            self.add_chat("jarvis", "Still working on the last command..."); return
        threading.Thread(target=self.listen_worker, daemon=True).start()

    def submit(self, text):
        text = text.strip()
        if not text:
            return
        if text.lower() in ("exit", "quit", "stop"):
            self.quit_app(); return
        if text.lower() == "listen":                    # same as clicking the mic button
            self.on_mic(); return
        if text.lower() in ("stop talking", "stop speaking", "be quiet", "quiet", "silence"):
            if voice is not None:
                voice.stop_speaking()
            return
        if not self.busy.acquire(blocking=False):
            self.add_chat("jarvis", "Still working on the last command..."); return
        self.cmd_hist.append(text); self.hist_pos = len(self.cmd_hist)
        self.add_chat("you", text)
        threading.Thread(target=self.worker, args=(text, False), daemon=True).start()

    # ---------------- worker side (background threads) ----------------
    def worker_confirm(self, msg):
        self.bridge.confirm.emit(msg)
        return self.answers.get()

    def worker_pick(self, title, items):
        self.bridge.pick.emit(title, list(items))
        return self.answers.get()

    def worker_file(self, title):
        self.bridge.file.emit(title)
        return self.answers.get()

    def worker_menu(self, title, options):
        self.bridge.menu.emit(title, list(options))
        return self.answers.get()

    def worker_ask(self, prompt):
        self.bridge.ask.emit(prompt)
        return self.answers.get()

    @staticmethod
    def init_com():
        try:
            import comtypes
            comtypes.CoInitialize()      # lets pyttsx3 speak from a background thread
        except Exception:
            pass

    def listen_worker(self):
        self.init_com()
        try:
            self.bridge.status.emit("Listening...")
            text = voice.listen()
        except Exception as e:
            self.bridge.chat.emit("jarvis", f"Microphone problem: {e}")
            self.bridge.status.emit("Online"); self.busy.release(); return
        if not text:
            self.bridge.chat.emit("jarvis", "I didn't hear anything.")
            self.bridge.status.emit("Online"); self.busy.release(); return
        self.bridge.chat.emit("you", text)
        self.worker(text, True)

    def worker(self, text, from_voice):
        self.init_com()
        try:
            self.bridge.status.emit("Thinking...")
            reply = self.handle(text)
            self.bridge.chat.emit("jarvis", reply)
            if voice is not None and (self.speak_on or from_voice):
                self.bridge.status.emit("Speaking...")
                voice.speak(reply)
        except Exception as e:
            self.bridge.chat.emit("jarvis", f"Something went wrong: {e}")
        finally:
            self.bridge.status.emit("Online")
            self.busy.release()

    def voice_command(self, text):
        low = text.lower().strip()
        if low == "paste":
            return "Just paste your multi-line question into the box below and press Enter."
        if not low.startswith("voice"):
            return None
        if voice is None:
            return f"Voice isn't available: {VOICE_ERR}"
        if low in ("voice on", "voice off"):
            self.speak_on = low == "voice on"
            self.bridge.voiceflag.emit(self.speak_on)
            return "Voice replies on." if self.speak_on else "Voice replies off."
        if low == "voice list":
            return voice.list_voices()
        m = re.fullmatch(r"voice speed (\d+)", low)
        if m:
            voice.set_speed(int(m.group(1))); return "Speaking speed updated."
        arg = text.strip()[5:].strip()
        if arg:
            ok, msg = voice.choose(arg)
            if ok:
                voice.speak("Hello, this is my new voice.")
            return msg
        return "Try: voice on | voice off | voice list | voice neerja | voice local | voice speed 170"

    def handle(self, text):
        vr = self.voice_command(text)
        if vr is not None:
            return vr
        forced = None
        if "\n" in text.strip():                       # multi-line = paste mode
            hint = " Use MySQL syntax." if "sql" in text.lower() else ""
            forced = {"action": "ask_coder", "query": "", "task": "",
                      "args": {"question": "Answer every part of the following question completely and in "
                                           "order, with a working example for each part." + hint + "\n\n" + text}}
            parts = [text]
        else:
            parts = jarvis.split_commands(text)
        if len(parts) > 1:
            print("Doing " + str(len(parts)) + " steps: " + " -> ".join(parts))
        replies = []
        for part in parts:
            data = forced or jarvis.quick_route(part) or jarvis.ask_llama(part, self.history)
            screen = isinstance(data, dict) and data.get("action") in SCREEN_ACTIONS
            if screen:
                self.bridge.hide_window.emit()          # minimize Amvi
                time.sleep(1.0)                          # wait for the window to disappear
            try:
                replies.append(jarvis.execute_command(data))
            finally:
                if screen:
                    self.bridge.show_window.emit()       # bring Amvi back
            if len(parts) > 1:
                time.sleep(1.5)
        reply = "\n".join(replies)
        self.history.append((text[:200], reply[:400]))
        more_tools.save_history(self.history)
        tools.LAST_REPLY = reply
        if forced:
            out = tools.WORKSPACE / "answers"; out.mkdir(exist_ok=True)
            f = out / f"answer_{int(time.time())}.txt"
            f.write_text("QUESTION:\n" + text + "\n\nANSWER:\n" + reply, encoding="utf-8")
            reply += f"\n\n[Saved to {f}]"
        return reply

    def stats_loop(self):
        while True:
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory().percent
            gpu, gtxt = -1.0, ""
            try:
                r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
                                    "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                   timeout=3, creationflags=0x08000000)
                u, tmp = r.stdout.strip().split(",")
                gpu, gtxt = float(u), f"GPU {tmp.strip()}\u00b0C"
            except Exception:
                pass
            b = psutil.sensors_battery()
            bat = f"Battery {b.percent:.0f}%{' \u26a1' if b.power_plugged else ''}" if b else ""
            self.bridge.stats.emit(cpu, ram, gpu, gtxt, bat)
            time.sleep(1.5)


LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amvi_error.log")


def _log_crash(exc_type, exc, tb):
    """With no console window, errors would vanish: save them and show a message box."""
    import traceback
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" +
                    "".join(traceback.format_exception(exc_type, exc, tb)) + "\n")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"Amvi hit an error:\n{exc}\n\nDetails: {LOGFILE}", "Amvi", 0x10)
        except Exception:
            pass


def _windows_setup():
    """No stray console windows: minimize ours, and stop child programs from flashing one."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 7)        # minimize without stealing focus
    except Exception:
        pass
    original = subprocess.Popen.__init__

    def patched(self, *a, **k):
        k["creationflags"] = k.get("creationflags", 0) | 0x08000000      # CREATE_NO_WINDOW
        original(self, *a, **k)
    subprocess.Popen.__init__ = patched


if __name__ == "__main__":
    sys.excepthook = _log_crash
    threading.excepthook = lambda a: _log_crash(a.exc_type, a.exc_value, a.exc_traceback)
    _windows_setup()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 10))
    win = Main()
    app.setQuitOnLastWindowClosed(win.tray is None)      # with a tray icon, closing hides to the tray
    if not ("--tray" in sys.argv and win.tray is not None):
        win.show()
    sys.exit(app.exec())
