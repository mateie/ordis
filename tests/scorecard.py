"""Grade Ordis's model-based decisions, so a different model can be compared before switching to it.

    uv run python tests/scorecard.py                      # the model Ordis uses now
    uv run python tests/scorecard.py qwen3:8b qwen3:14b   # several, side by side
    uv run python tests/scorecard.py qwen3:8b --only judge,watch
"""

import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]

import ollama  # noqa: E402

import Glitch  # noqa: E402
import Judge  # noqa: E402
from cases import GLITCH, JUDGE, LEARN, ROUTE, ROUTE_TOOLS, WATCH  # noqa: E402

NUM_CTX = 8192
# A voice call app holding the mic, as Ordis would see it
CALL = ["Discord"]
Result = tuple[int, int, list[str]]


def judge_suite(model: str) -> Result:
    """Talk with friends only happens in a call. Talk with Ordis happens either way, so it's checked in both."""

    right = total = 0
    misses: list[str] = []
    for said, heard, wake_word, for_ordis, ends in JUDGE:
        for mic in ([[], CALL] if for_ordis else [CALL]):
            got_for, got_ends = Judge.judge(model, NUM_CTX, said, heard, wake_word, mic=mic)
            checks = [("for Ordis", for_ordis, got_for)] + ([("ends chat", ends, got_ends)] if ends is not None else [])
            for label, want, got in checks:
                total += 1
                right += want == got
                if want != got:
                    misses.append(f"{heard!r}{' (in a call)' if mic else ''}: {label} should be {want}")
    return right, total, misses


def learn_suite(model: str) -> Result:
    """Teach one correction, then check a similar but different sentence. Scores how many end up right afterwards."""

    import tempfile

    import Lessons

    with tempfile.TemporaryDirectory() as folder:
        Lessons.DB_PATH = Path(folder) / "lessons.db"
        lessons = Lessons.Lessons()

        right = 0
        notes: list[str] = []
        for lesson, (said, heard, want) in LEARN:
            before, _ = Judge.judge(model, NUM_CTX, said, heard, False)
            lessons.save(lesson)
            after, _ = Judge.judge(model, NUM_CTX, said, heard, False, lessons.nearest(heard))
            right += after == want
            change = "fixed" if before != want and after == want else "broke" if before == want and after != want else \
                "still right" if after == want else "still wrong"
            notes.append(f"{heard!r}: {change}")
        lessons.db.close()

    return right, len(LEARN), notes


def watch_suite(model: str) -> Result:
    misses = [
        f"{watch!r} vs {event[:45]!r}: should {'alert' if want else 'stay quiet'}"
        for watch, event, want in WATCH
        if Judge.matches_watch(model, NUM_CTX, watch, event) != want
    ]
    return len(WATCH) - len(misses), len(WATCH), misses


def glitch_suite(model: str) -> Result:
    Glitch.recent.clear()
    Glitch.pool.clear()

    made: list[str | None] = []
    for sentence in GLITCH:
        if not Glitch.pool:
            Glitch.refill(model, NUM_CTX)
        made.append(Glitch.twist(sentence))
    return sum(m is not None for m in made), len(GLITCH), [f"  {m}" for m in made if m]


def route_suite(model: str) -> Result:
    from Apps import Apps
    from Files import Files
    from Memory import Memory
    from Reminders import Reminders
    from Router import Router
    from System import System
    from Util import Util
    from Warframe import Warframe
    from Web import Web

    tools: dict[str, Callable[..., Any]] = {
        **Apps().tools, **Files().tools, **Web().tools, **Memory().tools, **Util().tools, **System().tools,
        **Reminders(lambda text: None).tools, **Warframe(lambda text: None, lambda: model, lambda: True, NUM_CTX).tools,
    }
    router = Router(tools, model, NUM_CTX)
    while router.index is None:
        time.sleep(0.2)

    right = total = 0
    misses: list[str] = []
    for kind, texts in ROUTE.items():
        for text in texts:
            picked = set(router.pick(text) or [])
            ok = bool(ROUTE_TOOLS[kind] & picked) if kind else True
            total += 1
            right += ok
            if not ok:
                misses.append(f"{text!r} didn't reach any {kind} tool")
    return right, total, misses


def speed_suite(model: str) -> Result:
    """How long until Ordis starts talking, how fast he speaks, and how much of the model fits on the GPU."""

    from Brain import SYSTEM_PROMPT

    notes: list[str] = []
    for ask in ("how's the ship doing", "tell me something interesting about Warframe"):
        start = time.time()
        first = None
        stream = ollama.chat(  # pyright: ignore[reportUnknownMemberType]
            model=model, think=False, keep_alive=-1, stream=True, options={"num_ctx": NUM_CTX},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": ask}],
        )
        for chunk in stream:
            if first is None and chunk.message.content:
                first = time.time() - start
            if chunk.done:
                speed = (chunk.eval_count or 0) / max((chunk.eval_duration or 1) / 1e9, 1e-9)
                notes.append(f"{ask!r}: first words after {first or 0:.2f}s, {speed:.0f} tokens/s")

    running = next((m for m in ollama.ps().models if m.model == model), None)
    if running:
        on_gpu = (running.size_vram or 0) / max(running.size or 1, 1)
        notes.append(f"{(running.size or 0) / 1024**3:.1f} GB loaded, {on_gpu:.0%} on the GPU")
    return 1, 1, notes


SUITES: dict[str, Callable[[str], Result]] = {
    "speed": speed_suite,
    "judge": judge_suite,
    "learn": learn_suite,
    "watch": watch_suite,
    "glitch": glitch_suite,
    "route": route_suite,
}


def loaded() -> set[str]:
    return {m.model for m in ollama.ps().models}


def main():
    args = sys.argv[1:]
    only = list(SUITES)
    if "--only" in args:
        at = args.index("--only")
        only = args[at + 1].split(",")
        del args[at:at + 2]
    models = args
    if not models:
        from Brain import MODEL
        models = [MODEL]

    for model in models:
        was_loaded = model in loaded()
        print(f"\n=== {model} ===")
        for name in only:
            start = time.time()
            right, total, misses = SUITES[name](model)
            print(f"{name:7} {right:3}/{total:<3} ({right / total:4.0%})  {time.time() - start:5.1f}s")
            for miss in misses:
                print(f"          {miss}")

        # Don't leave a model sitting on the GPU just because it was tested
        if not was_loaded:
            ollama.generate(model=model, keep_alive=0)


if __name__ == "__main__":
    main()
