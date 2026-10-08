Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Jarvis"
sh.Run """D:\Jarvis\.venv\Scripts\pythonw.exe"" ""D:\Jarvis\amvi_gui.py""", 1, False
