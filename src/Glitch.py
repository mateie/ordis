import json
import random
import re
from collections import deque
from pathlib import Path
from typing import Any

import ollama

from Mouth import OUTBURST

VOICE_DATA = Path(__file__).parent.parent / "voice_data"
CANON_LINES = VOICE_DATA / "glitch_dataset" / "metadata.csv"
CANON_WORDS = VOICE_DATA / "glitch_only_dataset" / "metadata.csv"

GLITCH_PROMPT = (
    "Ordis is a cheerful ship AI with a damaged, murderous memory that leaks through in sudden outbursts. Write eight "
    "different outbursts: what a corrupted killer machine would blurt out mid-sentence. Each must be genuinely dark and "
    "one to three words long. Invent your own, never reuse one from the example."
)
DARK_PROMPT = "Is this phrase dark, violent, morbid or disturbing? Answer with only yes or no."
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"twists": {"type": "array", "items": {"type": "string", "maxLength": 60}, "minItems": 6, "maxItems": 10}},
    "required": ["twists"],
}
ATTEMPTS = 2
MIN_WORDS = 4
MIN_SPOT_LETTERS = 4
MAX_TWIST_WORDS = 6
RECENT = 8
POOL_SIZE = 8

recent: list[str] = []
pool: deque[str] = deque()


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def read_lines(path: Path) -> list[str]:
    try:
        return [line.split("|")[-1].strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as err:
        print(f"[Glitch] Couldn't read {path.name}: {err}")
        return []


def load_twists() -> list[str]:
    """The outbursts from real lines shaped 'before... TWIST... after'."""

    twists: list[str] = []
    for line in read_lines(CANON_LINES):
        pieces = [p.strip() for p in line.split("...")]
        if len(pieces) < 3 or not pieces[0] or not pieces[1] or not pieces[2]:
            continue

        twisted = pieces[1].strip(" ,!?.")
        if twisted:
            twists.append(twisted.lower())
    return twists


TWISTS = load_twists()
TAKEN = {w for twist in TWISTS + read_lines(CANON_WORDS) for w in words(twist) if len(w) > 3}

dark_twists: dict[str, list[str]] = {}


def prepare(model: str, num_ctx: int) -> list[str]:
    if model not in dark_twists:
        dark_twists[model] = [t for t in TWISTS if is_dark(t, model, num_ctx)] or TWISTS
    return dark_twists[model]


def is_dark(phrase: str, model: str, num_ctx: int) -> bool:
    try:
        response = ollama.chat( # pyright: ignore[reportUnknownMemberType]
            model=model,
            messages=[{"role": "system", "content": DARK_PROMPT}, {"role": "user", "content": phrase.lower()}],
            think=False,
            options={"num_ctx": num_ctx, "num_predict": 3, "temperature": 0},
            keep_alive=-1
        )
    except Exception as err:
        print(f"[Glitch] Darkness check failed: {err}")
        return False

    return (response.message.content or "").strip().lower().startswith("yes")


def write_twists(model: str, num_ctx: int) -> list[str]:
    """Ask the model for a batch of outbursts in one go."""

    messages = [
        {"role": "system", "content": GLITCH_PROMPT},
        {"role": "user", "content": "Write them."},
        {"role": "assistant", "content": json.dumps({"twists": prepare(model, num_ctx)})},
        {"role": "user", "content": "Write them."},
    ]

    try:
        response = ollama.chat( # pyright: ignore[reportUnknownMemberType]
            model=model,
            messages=messages,
            format=SCHEMA,
            think=False,
            options={"num_ctx": num_ctx, "num_predict": 200, "temperature": 0.9},
            keep_alive=-1
        )
        return [str(t) for t in json.loads(response.message.content or "{}").get("twists", [])]
    except Exception as err:
        print(f"[Glitch] Twist failed: {err}")
        return []


def refill(model: str, num_ctx: int):
    """Top the pool of ready outbursts back up. Slow, so it runs in the background between replies."""

    for _ in range(ATTEMPTS):
        if len(pool) >= POOL_SIZE:
            return

        for raw in write_twists(model, num_ctx):
            dark = " ".join(re.findall(r"[A-Z']+", raw.upper()))
            stale = TAKEN | {w for w in words(" ".join(recent)) if len(w) > 3}
            if len(pool) >= POOL_SIZE or not OUTBURST.search(dark) or len(dark.split()) > MAX_TWIST_WORDS:
                continue
            if words(dark) & stale or not is_dark(dark, model, num_ctx):
                continue

            pool.append(dark)
            recent.append(dark)
            del recent[:-RECENT]


def place(sentence: str, word: str, dark: str) -> str | None:
    """Put the twist right before a word, like 'your... EXTREMELY RISKY... bold decision'."""

    found = re.search(rf"\b{re.escape(word)}\b", sentence, re.I) if word else None
    if not found or not dark or len(dark.split()) > MAX_TWIST_WORDS:
        return None

    before = sentence[:found.start()].rstrip()
    text = (f"{before}... " if before else "") + f"{dark}... {sentence[found.start():]}"
    return text if OUTBURST.search(text) else None


def twist(sentence: str) -> str | None:
    """A glitched version of the sentence, or None if no outburst is ready or no word fits."""

    spots = [w for w in re.findall(r"[A-Za-z']+", sentence) if len(w) >= MIN_SPOT_LETTERS]
    if not pool or not spots:
        return None
    return place(sentence, random.choice(spots), pool.popleft())
