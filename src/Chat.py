import threading
import tkinter as tk
from collections import deque
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, TextIO

MAX_PENDING = 2000
MAX_LINES = 2000
POLL_MS = 100
BACKGROUND = "#101418"
TEXT = "#D8E4EA"
INPUT = "#1B2228"
ACCENT = "#38C6E8"
FONT = ("Consolas", 10)


class Transcript:
    """Stands in for the console: what's printed goes to the log, and to the chat window when it's open."""

    def __init__(self, log: TextIO):
        self.log = log
        self.pending: deque[str] = deque(maxlen=MAX_PENDING)

    def write(self, text: str) -> int:
        self.pending.append(text)
        return self.log.write(text)

    def flush(self):
        self.log.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.log, name)


class ChatWindow:
    """Typing to Ordis, with the same output the terminal would show. Closing it only hides it."""

    def __init__(self, transcript: Transcript, send: Callable[[str], None]):
        self.transcript = transcript
        self.send = send
        self.wanted = threading.Event()
        self.thread: threading.Thread | None = None

    def open(self):
        self.wanted.set()
        if self.thread is None:
            # Tk has to stay on the thread that created it
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        self.root = tk.Tk()
        self.root.title("Ordis")
        self.root.geometry("600x440")
        self.root.configure(bg=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)

        self.output = ScrolledText(self.root, state="disabled", wrap="word", bg=BACKGROUND, fg=TEXT, font=FONT, relief="flat", padx=10, pady=8)
        self.entry = tk.Entry(self.root, bg=INPUT, fg=TEXT, insertbackground=ACCENT, font=FONT, relief="flat")
        self.entry.pack(side="bottom", fill="x", padx=10, pady=10, ipady=6)
        self.output.pack(side="top", fill="both", expand=True)
        self.entry.bind("<Return>", self.submit)

        self.poll()
        self.root.mainloop()

    def poll(self):
        text = ""
        while self.transcript.pending:
            text += self.transcript.pending.popleft()

        if text:
            self.output.configure(state="normal")
            self.output.insert("end", text)
            extra = int(self.output.index("end").split(".")[0]) - MAX_LINES
            if extra > 0:
                self.output.delete("1.0", f"{extra}.0")
            self.output.configure(state="disabled")
            self.output.see("end")

        if self.wanted.is_set():
            self.wanted.clear()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.entry.focus_set()

        self.root.after(POLL_MS, self.poll)

    def submit(self, _: Any = None):
        text = self.entry.get().strip()
        self.entry.delete(0, "end")
        if text:
            self.send(text)
