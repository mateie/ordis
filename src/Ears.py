from collections import deque
from pathlib import Path
import queue
from threading import Event, Lock
import time
from typing import Any, cast

from faster_whisper import WhisperModel # pyright: ignore[reportMissingTypeStubs]
import numpy as np
import sounddevice as sd # pyright: ignore[reportMissingTypeStubs]
from numpy.typing import NDArray
from openwakeword.model import Model # pyright: ignore[reportMissingTypeStubs]
from openwakeword import utils # pyright: ignore[reportMissingTypeStubs]

WAKE_MODEL = Path(__file__).parent / "models" / "wakeword" / "ordis.onnx"
WHISPER_MODEL = "small.en"

THRESHOLD = 0.3
SILENCE_RMS = 500.0
HITS = 3

RATE = 16000
CHUNK = 1280
MAX_SECONDS = 10
SILENCE_CHUNKS = 10
BACKLOG_SECONDS = 5

class Ears:
    def __init__(self, debug: bool = False):
        utils.download_models(["no_pretrained_models"]) # Needed

        self.debug = debug
        self.mic = Lock()
        self.stream: Any = None
        self.heard: queue.Queue[NDArray[np.int16]] = queue.Queue()
        self.lead: list[NDArray[np.int16]] = []
        self.speech_end = 0.0
        self.wake_peak = 0.0

        self.wake: Any = Model(wakeword_models=[str(WAKE_MODEL)], inference_framework="onnx")
        self.whisper = self.load_whisper()

    def load_whisper(self) -> WhisperModel:
        """The GPU is many times faster, so use it whenever it's there."""

        try:
            return WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
        except Exception as err:
            print(f"[Ears] Couldn't load Whisper on the GPU, using the CPU: {err}")
            return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        segments, _ = self.whisper.transcribe(audio, language="en", vad_filter=True) # pyright: ignore[reportUnknownMemberType]

        return " ".join(segment.text.strip() for segment in segments).strip()

    def capture(self, indata: NDArray[np.int16], *_: Any):
        """Runs for every chunk from the mic, so nothing is lost while the rest of Ordis catches up."""

        if self.heard.full():
            self.heard.get_nowait()
        self.heard.put_nowait(indata.flatten())

    def close_mic(self):
        """Actually let go of the mic, once whatever is listening has stopped."""

        with self.mic:
            if self.stream:
                self.stream.close()
                self.stream = None

    def open_mic(self):
        """Start from a fresh stream with nothing queued. Also picks up a changed default mic."""

        if self.stream:
            self.stream.close()

        self.heard = queue.Queue(maxsize=int(BACKLOG_SECONDS * RATE / CHUNK))
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, callback=self.capture)
        self.stream.start()

    def wait_for_wake(self, active: Event) -> bool:
        """Block until the wake word is heard. Returns False if active gets cleared first.
        The mic stays open afterwards, so record picks up right where this stopped."""

        hits = 0
        peak = 0.0
        recent: deque[NDArray[np.int16]] = deque(maxlen=HITS)

        with self.mic:
            self.open_mic()
            self.wake.reset()
            while active.is_set():
                chunk = self.heard.get(timeout=1)
                recent.append(chunk)
                scores = cast(dict[str, float], self.wake.predict(chunk))
                top = max(scores.values())

                if self.debug and top > 0.1:
                    print(f"Wake score: {top:.3f}")

                hits = hits + 1 if top > THRESHOLD else 0
                peak = max(peak, top) if hits else 0.0
                if hits >= HITS:
                    self.wake_peak = peak
                    # The name has finished by the time it's detected, so the next word may already be in these chunks
                    self.lead = list(recent)
                    return True

        return False

    def record(self, idle: Event, wait: float = MAX_SECONDS, fresh: bool = False) -> str:
        """Record until you stop talking, return the transcript.
        Gives up if no speech starts within `wait` seconds or idle gets cleared.
        fresh skips anything said before this was called, for listening again after Ordis spoke."""

        print("Ordis > (listening...)", flush=True)

        frames, self.lead = self.lead, []
        heard_speech = False
        silent = 0
        wait_chunks = int(wait * RATE / CHUNK)

        with self.mic:
            if fresh or not self.stream:
                self.open_mic()

            for i in range(int(MAX_SECONDS * RATE / CHUNK)):
                if not idle.is_set():
                    return ""

                chunk = self.heard.get(timeout=1)
                frames.append(chunk)
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                if rms >= SILENCE_RMS:
                    heard_speech = True
                    silent = 0
                    self.speech_end = time.perf_counter()
                elif heard_speech:
                    silent += 1
                    if silent >= SILENCE_CHUNKS:
                        break
                elif i >= wait_chunks:
                    return ""

        if not heard_speech:
            return ""

        audio: NDArray[np.float32] = np.concatenate(frames).astype(np.float32) / 32768.0

        try:
            return self.transcribe(audio)
        except RuntimeError as err:
            print(f"[Ears] Whisper failed on the GPU, switching to the CPU: {err}")
            self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            return self.transcribe(audio)
