import ctypes
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

import psutil

from Apps import WINDOWS_DIR, protected_pids

CHECK_SECONDS = 10
GPU_BUSY = 40
CONFIRM_CHECKS = 2

user32 = ctypes.windll.user32
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def fullscreen_pid() -> int | None:
    """The process behind the window in front, if that window covers its whole screen."""

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    window = wintypes.RECT()
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not user32.GetWindowRect(hwnd, ctypes.byref(window)):
        return None
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info)):
        return None

    screen = info.rcMonitor
    covers = window.left <= screen.left and window.top <= screen.top and window.right >= screen.right and window.bottom >= screen.bottom
    if not covers:
        return None

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None

def front_app() -> str | None:
    """The app the Operator has in front right now."""

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    try:
        return Path(psutil.Process(pid.value).exe()).stem
    except psutil.Error:
        return None


def gpu_busy() -> bool:
    """Whether the GPU is working hard. If it can't be read, fullscreen alone decides."""

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True, creationflags=subprocess.CREATE_NO_WINDOW
        ).stdout
        return int(out.splitlines()[0].strip()) >= GPU_BUSY
    except Exception:
        return True


def game_candidate() -> psutil.Process | None:
    """A fullscreen app that isn't part of Windows or Ordis, while the GPU is busy."""

    pid = fullscreen_pid()
    if pid is None or pid in protected_pids():
        return None

    try:
        proc = psutil.Process(pid)
        if Path(proc.exe()).resolve().is_relative_to(WINDOWS_DIR):
            return None
    except psutil.Error:
        return None

    return proc if gpu_busy() else None


class Games:
    def __init__(self, on_change: Callable[[str | None], None], is_game: Callable[[str], bool]):
        self.on_change = on_change
        self.is_game = is_game
        self.verdicts: dict[str, bool] = {}
        self.playing: str | None = None
        self.game: psutil.Process | None = None
        self.seen: tuple[int, int] = (0, 0)

    def check(self) -> str | None:
        """The game being played. Once found, it stays the game until it closes, even when alt-tabbed."""

        if self.game is not None:
            if self.game.is_running():
                return Path(self.game.exe()).stem
            self.game = None

        candidate = game_candidate()
        if candidate is None:
            self.seen = (0, 0)
            return None

        count = self.seen[1] + 1 if self.seen[0] == candidate.pid else 1
        self.seen = (candidate.pid, count)
        if count < CONFIRM_CHECKS:
            return None

        name = Path(candidate.exe()).stem
        if name not in self.verdicts:
            self.verdicts[name] = self.is_game(name)
        if not self.verdicts[name]:
            return None

        self.game = candidate
        return name

    def loop(self, stop: threading.Event):
        while not stop.is_set():
            try:
                game = self.check()

                if (game is None) != (self.playing is None):
                    self.playing = game
                    self.on_change(game)
            except Exception as err:
                print(f"[Games] Check failed: {err}")

            stop.wait(CHECK_SECONDS)

    def start(self) -> threading.Event:
        stop = threading.Event()
        threading.Thread(target=self.loop, args=(stop,), daemon=True).start()

        return stop
