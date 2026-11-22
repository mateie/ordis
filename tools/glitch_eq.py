"""Measure how Ordis's glitch words differ in tone from his normal words, as an EQ curve for Mouth.

Usage:
    uv run python tools/glitch_eq.py <glitch clips folder> <words file>

    e.g. uv run python tools/glitch_eq.py voice_data/sorted/glitch voice_data/glitch_words.txt

The first run transcribes every clip with word timings and writes the words file (plus a .json
next to it with the timings). Open it and write the glitchy words in CAPS, without adding,
removing or reordering words. If Whisper misheard a glitch word, capitalize it where it stands.

The next run measures only the marked words against the normal words in the same clips,
so the comparison isn't watered down by the calm parts of each clip, and writes
src/models/glitch_eq.json for Mouth.
"""

import json
import sys
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from prep_voice_dataset import RATE, WHISPER_RATE, audio_files, decode_audio, resample

FFT_SIZE = 1024
HOP = 256
BANDS = 20
MIN_FREQ = 80
MAX_GAIN_DB = 12.0
OUTPUT = Path(__file__).parent.parent / "src" / "models" / "glitch_eq.json"

def transcribe(clips: Path, words_file: Path):
    """Write every clip's words in lowercase for marking, and their timings next to it."""

    from faster_whisper import WhisperModel # pyright: ignore[reportMissingTypeStubs]

    whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    timings: dict[str, list[dict[str, float | str]]] = {}
    lines: list[str] = []

    for path in audio_files(clips):
        audio = decode_audio(path, RATE)
        segments, _ = whisper.transcribe(resample(audio, WHISPER_RATE), language="en", word_timestamps=True) # pyright: ignore[reportUnknownMemberType]
        words = [{"word": w.word.strip(), "start": w.start, "end": w.end} for s in segments for w in (s.words or [])]
        timings[path.name] = words
        lines.append(f"{path.name}|{' '.join(str(w['word']).lower() for w in words)}")
        print(lines[-1])

    words_file.with_suffix(".json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
    words_file.write_text(
        "# Write the glitchy words in CAPS. Don't add, remove or reorder words.\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"\nNow mark the glitch words in {words_file} and run this again.")

def is_glitch(token: str) -> bool:
    letters = [c for c in token if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)

def band_shapes(audio: NDArray[np.float32]) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    """Per frame: energy in each band as a share of the frame's total, frame center times, and which frames are audible."""

    count = 1 + (audio.size - FFT_SIZE) // HOP if audio.size >= FFT_SIZE else 0
    index = np.arange(FFT_SIZE)[None, :] + HOP * np.arange(count)[:, None]
    power = np.abs(np.fft.rfft(audio[index] * np.hanning(FFT_SIZE), axis=1)) ** 2 + 1e-12

    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / RATE)
    edges = np.geomspace(MIN_FREQ, RATE / 2, BANDS + 1)
    bands = np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1)

    energy = power.sum(axis=1)
    times = (np.arange(count) * HOP + FFT_SIZE / 2) / RATE
    return bands / bands.sum(axis=1, keepdims=True), times, energy > energy.max() * 0.01

def measure(clips: Path, words_file: Path):
    timings = json.loads(words_file.with_suffix(".json").read_text(encoding="utf-8"))
    glitch_shapes: list[NDArray[np.float64]] = []
    normal_shapes: list[NDArray[np.float64]] = []

    for line in words_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "|" not in line:
            continue

        name, text = line.split("|", 1)
        tokens, words = text.split(), timings.get(name, [])
        if len(tokens) != len(words):
            print(f"skip {name}: {len(tokens)} words but Whisper heard {len(words)}, was a word added or merged?")
            continue

        shapes, times, audible = band_shapes(decode_audio(clips / name, RATE))
        glitch = np.zeros(times.size, dtype=bool)
        normal = np.zeros(times.size, dtype=bool)
        for token, word in zip(tokens, words):
            inside = (times >= word["start"]) & (times <= word["end"])
            (glitch if is_glitch(token) else normal)[inside] = True

        glitch &= audible
        normal &= audible & ~glitch
        # Only clips with both, so each glitch word is compared to the same recording's normal voice
        if glitch.sum() >= 3 and normal.sum() >= 3:
            glitch_shapes.append(shapes[glitch].mean(axis=0))
            normal_shapes.append(shapes[normal].mean(axis=0))

    if not glitch_shapes:
        sys.exit("No clip has both marked glitch words and normal words.")

    edges = np.geomspace(MIN_FREQ, RATE / 2, BANDS + 1)
    centers = np.sqrt(edges[:-1] * edges[1:])
    gains = np.clip(10 * np.log10(np.mean(glitch_shapes, axis=0) / np.mean(normal_shapes, axis=0)), -MAX_GAIN_DB, MAX_GAIN_DB)

    OUTPUT.write_text(json.dumps({"freqs": centers.round(1).tolist(), "gains_db": gains.round(2).tolist()}, indent=2))
    for freq, gain in zip(centers, gains):
        print(f"{freq:7.0f} Hz  {gain:+6.2f} dB")
    print(f"\nMeasured {len(glitch_shapes)} clips, saved to {OUTPUT}")

def main():
    clips, words_file = Path(sys.argv[1]), Path(sys.argv[2])
    if words_file.with_suffix(".json").exists() and words_file.exists():
        measure(clips, words_file)
    else:
        transcribe(clips, words_file)

if __name__ == "__main__":
    main()
