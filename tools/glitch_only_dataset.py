"""Cut only the marked glitch words out of the glitch clips, as a Piper dataset with no calm speech in it.

Usage:
    uv run python tools/glitch_only_dataset.py <glitch clips folder> <words file> <output folder>

    e.g. uv run python tools/glitch_only_dataset.py voice_data/sorted/glitch voice_data/glitch_words.txt voice_data/glitch_only_dataset

Uses the CAPS marks and word timings from tools/glitch_eq.py. Every run of CAPS words becomes
one clip, cut halfway into the gap to the neighbouring words so no calm speech leaks in.

The transcripts are Whisper's words, so read metadata.csv afterwards and fix misheard ones.
"""

import json
import sys
from pathlib import Path

from glitch_eq import is_glitch
from prep_voice_dataset import RATE, decode_audio, save_wav

PAD = 0.12
# Whisper ends drawn-out glitch words too early, so give the end more room
END_PAD = 0.35
MIN_SECONDS = 0.3

def glitch_runs(tokens: list[str]) -> list[tuple[int, int]]:
    """Index ranges of consecutive CAPS tokens."""

    runs: list[tuple[int, int]] = []
    for i, token in enumerate(tokens):
        if not is_glitch(token):
            continue
        if runs and runs[-1][1] == i:
            runs[-1] = (runs[-1][0], i + 1)
        else:
            runs.append((i, i + 1))
    return runs

def main():
    clips, words_file, output = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    timings = json.loads(words_file.with_suffix(".json").read_text(encoding="utf-8"))

    wavs = output / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    total = 0.0

    for line in words_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "|" not in line:
            continue

        name, text = line.split("|", 1)
        tokens, words = text.split(), timings.get(name, [])
        if len(tokens) != len(words):
            print(f"skip {name}: {len(tokens)} words but Whisper heard {len(words)}")
            continue

        audio = decode_audio(clips / name, RATE)
        for a, b in glitch_runs(tokens):
            start, end = words[a]["start"] - PAD, words[b - 1]["end"] + END_PAD
            # Start halfway from the calm word before, end right where the next calm word starts
            if a > 0:
                start = max(start, (words[a - 1]["end"] + words[a]["start"]) / 2)
            if b < len(words):
                end = min(end, words[b]["start"])
            else:
                end = audio.size / RATE

            piece = audio[max(int(start * RATE), 0):int(end * RATE)]
            if piece.size < MIN_SECONDS * RATE:
                print(f"skip {name} '{' '.join(tokens[a:b])}': too short")
                continue

            out = f"ordis_glitch_only_{len(rows) + 1:04d}.wav"
            save_wav(wavs / out, piece)
            rows.append(f"{out}|{' '.join(tokens[a:b]).lower()}")
            total += piece.size / RATE
            print(f"{out} ({piece.size / RATE:.2f}s) from {name}: {' '.join(tokens[a:b])}")

    (output / "metadata.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n{len(rows)} clips, {total:.1f} seconds. Fix misheard words in metadata.csv.")

if __name__ == "__main__":
    main()
