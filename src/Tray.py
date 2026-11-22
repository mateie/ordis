import ctypes
import os
import sys
import threading
import time
import traceback
import winreg
from pathlib import Path
from typing import Any, Callable

import ollama
import pystray
from PIL import Image, ImageDraw

from Chat import ChatWindow, Transcript
from Console import already_running

LOG_PATH = Path(os.environ["LOCALAPPDATA"]) / "Ordis" / "ordis.log"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Ordis"

ICON_SIZE = 64
STARTING = "#E0A030"
LISTENING = "#38C6E8"
PAUSED = "#8A8A8A"
BROKEN = "#E05050"
OLLAMA_RETRY_SECONDS = 2
WM_LBUTTONDBLCLK = 0x0203


def icon_image(color: str) -> Image.Image:
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, ICON_SIZE - 4, ICON_SIZE - 4), outline=color, width=8)
    draw.ellipse((22, 22, ICON_SIZE - 22, ICON_SIZE - 22), fill=color)
    return image


def starts_with_windows() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except FileNotFoundError:
        return False


def set_starts_with_windows(on: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{Path(sys.executable).with_name("ordis-tray.exe")}"')
        else:
            winreg.DeleteValue(key, APP_NAME)


class ClickableIcon(pystray.Icon):
    """pystray only reports single clicks, and a double click would set those off twice, so nothing uses them."""

    on_double_click: Callable[[], None]

    def _on_notify(self, wparam: int, lparam: int):
        if lparam == WM_LBUTTONDBLCLK:
            self.on_double_click()
        else:
            super()._on_notify(wparam, lparam) # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


class Tray:
    """Ordis's icon by the clock, for running him without a terminal."""

    def __init__(self, transcript: Transcript):
        self.brain: Any = None
        self.status = "Starting"
        self.paused = False
        self.chat = ChatWindow(transcript, self.send)

        self.icon = ClickableIcon(APP_NAME, icon_image(STARTING), f"{APP_NAME}: {self.status}", pystray.Menu(
            pystray.MenuItem(lambda _: self.status, None, enabled=False),
            pystray.MenuItem(
                lambda _: "Resume" if self.paused else "Pause", self.toggle,
                enabled=lambda _: self.brain is not None # pyright: ignore[reportArgumentType]
            ),
            pystray.MenuItem("Type to Ordis", self.chat.open),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open log", self.open_log),
            pystray.MenuItem("Start with Windows", self.toggle_startup, checked=lambda _: starts_with_windows()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        ))
        self.icon.on_double_click = self.chat.open

    def send(self, text: str):
        if self.brain is None:
            print("Ordis is still starting.")
            return

        print(f"You > {text}")
        self.brain.typed(text)

    def show(self, status: str, color: str):
        self.status = status
        self.icon.icon = icon_image(color)
        self.icon.title = f"{APP_NAME}: {status}"
        self.icon.update_menu()

    def attach(self, brain: Any):
        self.brain = brain
        self.show("Listening", LISTENING)

    def toggle(self):
        # Pausing waits for the mic to be free, which would freeze the menu
        threading.Thread(target=self.switch, daemon=True).start()

    def switch(self):
        self.paused = not self.paused
        if self.paused:
            self.brain.pause()
            self.show("Paused, mic off", PAUSED)
        else:
            self.brain.resume()
            self.show("Listening", LISTENING)

    def open_log(self):
        os.startfile(LOG_PATH)

    def toggle_startup(self):
        set_starts_with_windows(not starts_with_windows())
        self.icon.update_menu()

    def quit(self):
        if self.brain is None:
            os._exit(0)

        self.show("Shutting down", STARTING)
        self.brain.mouth.stop()
        self.brain.inbox.put(("shut down", None))


def wait_for_ollama(tray: Tray):
    """Ollama starts with Windows too, so it may not be up yet."""

    while True:
        try:
            ollama.list()
            return
        except Exception:
            tray.show("Waiting for Ollama", STARTING)
            time.sleep(OLLAMA_RETRY_SECONDS)


def open_transcript() -> Transcript:
    """There's no console here, so everything printed goes to a log. The last run's is kept next to it."""

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists():
        LOG_PATH.replace(LOG_PATH.with_suffix(".previous.log"))

    transcript = Transcript(open(LOG_PATH, "w", encoding="utf-8", errors="replace", buffering=1))
    sys.stdout = sys.stderr = transcript
    return transcript


def main():
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, "Ordis is already running. Look for the icon by the clock.", APP_NAME, 0)
        return

    tray = Tray(open_transcript())
    threading.Thread(target=tray.icon.run, daemon=True).start()

    try:
        wait_for_ollama(tray)

        from Brain import Brain

        brain = Brain()
        threading.Thread(target=lambda: (brain.ready.wait(), tray.attach(brain)), daemon=True).start()
        brain.run(console=False)
    except Exception:
        traceback.print_exc()
        tray.show("Stopped, see the log", BROKEN)
        threading.Event().wait()

    os._exit(0)
