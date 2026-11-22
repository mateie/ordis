import hashlib
import inspect
import json
import threading
from typing import Any, Callable

import numpy as np
import ollama
from numpy.typing import NDArray

from Memory import DB_PATH, embed

CACHE_PATH = DB_PATH.parent / "router_examples.json"
EXAMPLE_PROMPT = (
    "Write 8 short, varied things a user might say out loud to a voice assistant on their PC that would need "
    "this tool. Casual spoken phrasing, different wording each time, no tool names."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"requests": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 10}},
    "required": ["requests"],
}
TOP_K = 25
MIN_SCORE = 0.45


def describe(name: str, tool: Callable[..., Any]) -> str:
    return f"{name.replace('_', ' ')}: {inspect.getdoc(tool) or ''}"


class Router:
    """Picks which tools a message needs, so small models only see a few at a time.
    It learns each tool from examples the model writes from the tool's own description."""

    def __init__(self, tools: dict[str, Callable[..., Any]], model: str, num_ctx: int):
        self.tools = tools
        self.index: tuple[list[str], NDArray[np.float32]] | None = None

        threading.Thread(target=self.learn, args=(model, num_ctx), daemon=True).start()

    def generate(self, description: str, model: str, num_ctx: int) -> list[str]:
        response = ollama.chat( # pyright: ignore[reportUnknownMemberType]
            model=model,
            messages=[
                {"role": "system", "content": EXAMPLE_PROMPT},
                {"role": "user", "content": description}
            ],
            format=SCHEMA,
            think=False,
            options={"num_ctx": num_ctx, "temperature": 0.7},
            keep_alive=-1
        )
        return [str(r) for r in json.loads(response.message.content or "{}").get("requests", [])]

    def learn(self, model: str, num_ctx: int):
        """Get example requests for every tool, writing new ones only when a tool's description changed."""

        try:
            cache: dict[str, Any] = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}

        names: list[str] = []
        texts: list[str] = []
        for name, tool in self.tools.items():
            description = describe(name, tool)
            key = hashlib.sha1(description.encode()).hexdigest()

            entry: dict[str, Any] = cache.get(name) or {}
            if entry.get("key") != key:
                try:
                    entry = {"key": key, "examples": self.generate(description, model, num_ctx)}
                    cache[name] = entry
                except Exception as err:
                    print(f"[Router] Couldn't write examples for {name}: {err}")
                    entry = {"key": key, "examples": []}

            examples: list[str] = entry["examples"]
            names += [name] * (len(examples) + 1)
            texts += [description, *examples]

        try:
            CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        except OSError as err:
            print(f"[Router] Couldn't save examples: {err}")

        try:
            self.index = (names, embed(texts, "search_query: "))
        except Exception as err:
            print(f"[Router] Couldn't embed examples, every message gets every tool: {err}")

    def pick(self, text: str) -> list[str] | None:
        """The tools this message needs, best first. None means 'send everything'."""

        if self.index is None:
            return None
        names, vectors = self.index

        try:
            scores = vectors @ embed([text], "search_query: ")[0]
        except Exception as err:
            print(f"[Router] Routing failed, sending every tool: {err}")
            return None

        picked: list[str] = []
        for i in np.argsort(-scores)[:TOP_K]:
            if scores[i] < MIN_SCORE:
                break
            name = names[int(i)]
            if name not in picked:
                picked.append(name)
        return picked
