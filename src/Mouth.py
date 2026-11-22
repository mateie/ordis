import queue
import threading

import numpy as np
from numpy.typing import NDArray
from piper import PiperVoice
from piper.config import SynthesisConfig

import sounddevice as sd # pyright: ignore[reportMissingTypeStubs]
from pathlib import Path
import json
import random
import re
import time
from typing import Callable

VOICE =Path(__file__).parent / "models" / "voices" / "en_US-ordis-medium.onnx"
GLITCH_VOICE = Path(__file__).parent / "models" / "voices" / "en_US-ordis_glitch-medium.onnx"

EFFECT = True
STUTTER_CHANCE = 0
ONSET_REPEATS = (1, 2)
TAIL_REPEAT_CHANCE = 0.5
LEAD_IN_REPEATS = (1, 2)

CORRUPT_LENGTH = 1.4
CORRUPT_SLOWDOWN = 1.1
CORRUPT_DRIVE = 2.5
CRUSH_HOLD = 2
CRUSH_LEVELS = 10

LONG_PAUSE = 0.4
SHORT_PAUSE = 0.15
GLITCH_PAUSE = 0.08

GLITCH_DEEPEN = (0.0, 3.0)

OUTBURST = re.compile(r"((?:\b[A-Z][A-Z']+\b[\s,!.?]*){2,}|\b[A-Z][A-Z']{4,}\b[\s,!.?]*)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
CLEANUP = re.compile(r"https?://\S+|[*`#>~]")

EQ_PATH = Path(__file__).parent / "models" / "glitch_eq.json"
EQ_STRENGTH = 1.0
GLITCH_EQ = json.loads(EQ_PATH.read_text()) if EQ_PATH.exists() else None

def to_float(audio: NDArray[np.int16]) -> NDArray[np.float64]:
    return audio.astype(np.float64) / 32768.0

def to_int16(x: NDArray[np.float64]) -> NDArray[np.int16]:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 1.0:
        x = x / peak
    return (x * 32767).astype(np.int16)

def cephalon(audio: NDArray[np.int16], rate: int) -> NDArray[np.int16]:
    """Ordis's digital sheen: a wobbling chorus plus a short metallic comb filter."""

    x = to_float(audio)
    if x.size == 0:
        return audio

    n = np.arange(x.size, dtype=np.float64)
    wobble = (0.010 + 0.002 * np.sin(2 * np.pi * 0.7 * n / rate)) * rate
    chorus = np.interp(n - wobble, n, x, left=0.0)

    ring = int(0.005 * rate)
    comb = x.copy()
    comb[ring:] += 0.4 * x[:-ring]

    return to_int16(0.65 * comb + 0.35 * chorus)

def stutter(audio: NDArray[np.int16], rate: int, start: int | None = None, repeats: tuple[int, int] = (2, 4)) -> NDArray[np.int16]:
    """Repeat a tiny slice a few times, like a skipping data stream. Random spot unless start is given."""

    size = int(rate * random.uniform(0.04, 0.08))
    if audio.size < size * 4:
        return audio

    if start is None:
        start = random.randrange(audio.size // 4, audio.size - size)
    start = min(max(start, 0), audio.size - size)

    piece = audio[start:start + size]
    return np.concatenate([audio[:start], *[piece] * random.randint(*repeats), audio[start:]])

def voiced_span(audio: NDArray[np.int16]) -> tuple[int, int]:
    """First and last sample where speech is audible, skipping Piper's silent padding."""

    level = np.abs(audio.astype(np.int32))
    loud = np.nonzero(level > level.max() * 0.1)[0]
    return (int(loud[0]), int(loud[-1])) if loud.size else (0, audio.size)

def trim(audio: NDArray[np.int16], rate: int, start: bool = True, end: bool = True) -> NDArray[np.int16]:
    """Cut Piper's silent padding off one or both ends, keeping a hair of margin so soft sounds aren't clipped."""

    if audio.size == 0:
        return audio

    first, last = voiced_span(audio)
    margin = int(rate * 0.03)
    return audio[max(0, first - margin) if start else 0:min(audio.size, last + margin) if end else audio.size]

def match_eq(x: NDArray[np.float64], rate: int) -> NDArray[np.float64]:
    """Shape the tone to match the real glitch words, using the curve from tools/glitch_eq.py."""

    if GLITCH_EQ is None:
        return x

    freqs = np.fft.rfftfreq(x.size, 1 / rate)
    gains = np.interp(freqs, GLITCH_EQ["freqs"], GLITCH_EQ["gains_db"]) * EQ_STRENGTH # type: ignore
    return np.fft.irfft(np.fft.rfft(x) * 10 ** (gains / 20), n=x.size) # type: ignore

def corrupt(audio: NDArray[np.int16], rate: int) -> NDArray[np.int16]:
    """A little deeper, distorted and glitch-toned, stuttering on the first sound like 'B-B-BLEEDING'."""

    x = to_float(audio)
    if x.size == 0:
        return audio

    slowed = np.linspace(0, x.size - 1, int(x.size * CORRUPT_SLOWDOWN))
    x = np.interp(slowed, np.arange(x.size), x)

    x = np.tanh(x * CORRUPT_DRIVE) / np.tanh(CORRUPT_DRIVE)
    x = np.repeat(x[::CRUSH_HOLD], CRUSH_HOLD)[:x.size]
    x = np.round(x * CRUSH_LEVELS) / CRUSH_LEVELS

    x = match_eq(x, rate)

    return glitch_stutter(to_int16(x), rate)

def deepen(audio: NDArray[np.int16], factor: float) -> NDArray[np.int16]:
    """Lower the pitch by stretching the audio, which also makes it factor times longer."""

    stretched = np.linspace(0, audio.size - 1, int(audio.size * factor))
    return np.interp(stretched, np.arange(audio.size), audio).astype(np.int16)

def glitch_stutter(audio: NDArray[np.int16], rate: int) -> NDArray[np.int16]:
    """Repeat the first sound like 'B-B-BLEEDING', and sometimes the last one."""

    if audio.size == 0:
        return audio

    first, last = voiced_span(audio)

    if random.random() < TAIL_REPEAT_CHANCE:
        audio = stutter(audio, rate, start=last - int(rate * 0.06), repeats=(1, 2))

    return stutter(audio, rate, start=first, repeats=ONSET_REPEATS)

class Mouth:
    def __init__(self):
        self.voice = PiperVoice.load(VOICE)
        self.glitch_voice = PiperVoice.load(GLITCH_VOICE) if GLITCH_VOICE.exists() else None
        self.rate = 22050
        self.acks: list[NDArray[np.int16]] = []

        self.buffer = ""
        self.sentences: queue.Queue[str] = queue.Queue()
        self.audio: queue.Queue[tuple[str, NDArray[np.int16]]] = queue.Queue()
        self.stopped = threading.Event()
        self.rewrite_next: Callable[[str], str | None] | None = None
        self.spoke_at = 0.0

        threading.Thread(target=self.synth_loop, daemon=True).start()
        threading.Thread(target=self.play_loop, daemon=True).start()
        
    def synth_loop(self):
        while True:
            sentence = self.sentences.get()
            try:
                if self.rewrite_next:
                    rewritten = self.rewrite_next(sentence)
                    if rewritten:
                        self.rewrite_next = None
                        sentence = rewritten
                        print(f"[Glitch] {sentence}")

                self.audio.put((sentence, self.say(sentence)))
            except Exception as err:
                print(f"[Mouth] Couldn't say '{sentence}': {err}")
            finally:
                self.sentences.task_done()
                
    def play_loop(self):
        while True:
            _, audio = self.audio.get()
            try:
                if not self.stopped.is_set():
                    self.spoke_at = self.spoke_at or time.perf_counter()
                    self.play(audio)
            except Exception as err:
                print(f"[Mouth] Couldn't play audio: {err}")
            finally:
                self.audio.task_done()
                
    def queue_sentence(self, text: str):
        text = CLEANUP.sub("", text).strip()
        if text and not self.stopped.is_set():
            self.sentences.put(text)
            
    def stop(self):
        """Cut Ordis off mid-reply: drop everything queued and stop the current sentence."""

        self.stopped.set()
        self.buffer = ""

        for pending in (self.sentences, self.audio):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
                pending.task_done()

        sd.stop()

    def resume(self):
        self.stopped.clear()

            
    def feed(self, chunk: str):
        """Add streamed text and start speaking each sentence as soon as it's complete."""

        self.buffer += chunk
        *done, self.buffer = SENTENCE_END.split(self.buffer)

        for sentence in done:
            self.queue_sentence(sentence)
            
    def finish(self):
        """Queue whatever is left in the buffer, like a last sentence with no space after it."""

        self.queue_sentence(self.buffer)
        self.buffer = ""

    def wait(self):
        """Block until everything queued has been spoken."""

        self.sentences.join()
        self.audio.join()
  
    def render(self, text: str, length_scale: float | None = None, voice: PiperVoice | None = None) -> NDArray[np.int16]:
        """Plain Piper speech. length_scale above 1 speaks slower without changing the pitch."""

        parts: list[NDArray[np.int16]] = []
        for chunk in (voice or self.voice).synthesize(text, SynthesisConfig(length_scale=length_scale)):
            self.rate = chunk.sample_rate
            parts.append(chunk.audio_int16_array)

        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)

    def say(self, text: str, glitchy: bool = True) -> NDArray[np.int16]:
        """Render one sentence with Ordis's effects applied."""

        if not EFFECT:
            return self.render(text)

        parts: list[NDArray[np.int16]] = []
        spoken: int | None = None
        pause = 0.0
        pieces = OUTBURST.split(text)
        for i, part in enumerate(pieces):
            if not part.strip():
                continue

            is_glitch = bool(i % 2)
            before_glitch = i + 1 < len(pieces) and bool(pieces[i + 1].strip())
            after_glitch = i > 0 and not is_glitch
            near_glitch = is_glitch or before_glitch
            end = part.rstrip()
            if near_glitch:
                part = end.rstrip(".…").strip() or end

            if is_glitch:
                if spoken is not None and pause != LONG_PAUSE and pause != GLITCH_PAUSE:
                    _, last = voiced_span(parts[spoken])
                    parts[spoken] = stutter(parts[spoken], self.rate, start=last - int(self.rate * 0.06), repeats=LEAD_IN_REPEATS)
                if self.glitch_voice:
                    factor = 2 ** (random.uniform(*GLITCH_DEEPEN) / 12)
                    audio = deepen(self.render(part.lower(), 1 / factor, self.glitch_voice), factor)
                    audio = glitch_stutter(trim(audio, self.rate), self.rate)
                else:
                    audio = corrupt(trim(self.render(part.lower(), CORRUPT_LENGTH), self.rate), self.rate)
            else:
                audio = self.render(part)
                if before_glitch or after_glitch:
                    audio = trim(audio, self.rate, start=after_glitch, end=before_glitch)
            parts.append(audio)

            spoken = len(parts) - 1
            if near_glitch and end.endswith(("...", "…")):
                pause = GLITCH_PAUSE
            else:
                pause = LONG_PAUSE if end.endswith(("...", "…")) else SHORT_PAUSE if end.endswith(",") else 0
            if pause:
                parts.append(np.zeros(int(self.rate * pause), dtype=np.int16))

        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)

        if glitchy and random.random() < STUTTER_CHANCE:
            audio = stutter(audio, self.rate)

        return audio

    def play(self, audio: NDArray[np.int16]):
        if audio.size:
            audio = np.concatenate([audio, np.zeros(self.rate // 4, dtype=np.int16)])
            sd.play(audio, self.rate) # pyright: ignore[reportUnknownMemberType]
            sd.wait()

    def set_acks(self, lines: list[str]):
        """Render the lines Ordis answers his name with, ready to play instantly."""

        self.acks = [self.say(line, glitchy=False) for line in lines]

    def acknowledge(self):
        """Say a 'Yes, Operator?' line without waiting for it to finish, so listening starts right away."""

        if self.acks:
            sd.play(random.choice(self.acks), self.rate) # pyright: ignore[reportUnknownMemberType]

    def speak(self, text: str):
        self.resume()
        self.feed(text)
        self.finish()
        self.wait()
