import ctypes
import threading
from typing import Any, TextIO

PROMPT = "You > "
CLEAR_LINE = "\r\033[K"
INSTANCE_NAME = "OrdisAlreadyRunning"
ERROR_ALREADY_EXISTS = 183

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
instance: Any = None


def already_running() -> bool:
    """Two copies would both answer the mic. Windows frees this name when Ordis exits, even after a crash."""

    global instance
    instance = kernel32.CreateMutexW(None, False, INSTANCE_NAME)
    return ctypes.get_last_error() == ERROR_ALREADY_EXISTS


class PromptKeeper:
    """Wraps the console so anything printed lands above the 'You >' prompt instead of after it."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self.lock = threading.Lock()
        self.at_prompt = False

    def write(self, text: str) -> int:
        if not text:
            return 0

        with self.lock:
            if self.at_prompt:
                self.stream.write(CLEAR_LINE)
                self.at_prompt = False

            self.stream.write(text)

            if text.endswith("\n"):
                self.stream.write(PROMPT)
                self.at_prompt = True

            self.stream.flush()

        return len(text)

    def flush(self):
        self.stream.flush()
