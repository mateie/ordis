"""Listen to voice clips one by one and sort them with single key presses.

Usage:
    uv run python tools/sort_clips.py <clips folder> <output folder>

Keys (you don't have to wait for a clip to finish playing):
    c   calm, clean speech           -> <output>/calm
    g   glitchy or angry             -> <output>/glitch
    x   trash, unusable              -> <output>/trash
    s   split at pauses, then sort each piece on its own
    r   replay
    u   undo the last move
    q   quit (files are moved as you go, so rerunning picks up where you left off)
"""

import argparse
import msvcrt
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
import sounddevice as sd # pyright: ignore[reportMissingTypeStubs]

from prep_voice_dataset import AUDIO_EXTS, RATE, decode_audio, pause_spans, save_wav

FOLDERS = {"c": "calm", "g": "glitch", "x": "trash"}

def split_at_pauses(audio: NDArray[np.float32]) -> list[NDArray[np.float32]]:
    """Cut a clip wherever there's a pause."""

    return [audio[a:b] for a, b in pause_spans(audio)]

def move(src: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return src.rename(folder / src.name)

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clips", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    queue = sorted(p for p in args.clips.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    history: list[tuple[Path, Path]] = []
    print(__doc__.split("Keys", 1)[1])

    while queue:
        clip = queue[0]
        audio = decode_audio(clip, RATE)
        print(f"[{len(queue)} left] {clip.name} ({audio.size / RATE:.1f}s) > ", end="", flush=True)
        sd.play(audio, RATE) # pyright: ignore[reportUnknownMemberType]

        key = msvcrt.getwch().lower()
        sd.stop()
        print(key)

        if key == "q":
            break

        if key in FOLDERS:
            history.append((clip, move(clip, args.output / FOLDERS[key])))
            queue.pop(0)

        elif key == "u":
            if history:
                src, dest = history.pop()
                dest.rename(src)
                queue.insert(0, src)

        elif key == "s":
            pieces = split_at_pauses(audio)
            if len(pieces) < 2:
                print("  no pauses to split at, trim this one by hand or pick c/g/x")
                continue

            paths = [clip.with_name(f"{clip.stem}_p{n}.wav") for n in range(1, len(pieces) + 1)]
            for path, piece in zip(paths, pieces):
                save_wav(path, piece)

            move(clip, args.output / "split_originals")
            queue[0:1] = paths
            print(f"  split into {len(pieces)} pieces")

    print("Done!" if not queue else f"Stopped, {len(queue)} clips left.")

if __name__ == "__main__":
    main()
