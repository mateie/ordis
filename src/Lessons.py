import sqlite3
import threading

import numpy as np

from Judge import Lesson
from Memory import DB_PATH, embed

LESSON_SCORE = 0.55
MAX_LESSONS = 4
RECENT = 10


class Lessons:
    """Remembers the Operator's corrections, like 'that wasn't for you', and shows similar ones to the model next time."""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS lessons ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, said TEXT NOT NULL, heard TEXT NOT NULL, "
                "for_ordis INTEGER NOT NULL, ends INTEGER NOT NULL, embedding BLOB NOT NULL)"
            )
            self.db.commit()

        self.recent: list[Lesson] = []
        self.tools = {f.__name__: f for f in (self.not_meant_for_me, self.was_meant_for_me, self.wasnt_finished)}

    def note(self, decision: Lesson):
        """Remember a decision Brain just made about something the Operator said."""

        self.recent.append(decision)
        del self.recent[:-RECENT]

    def save(self, lesson: Lesson):
        said, heard, for_ordis, ends = lesson
        vector = embed([heard], "search_query: ")[0]
        with self.lock:
            self.db.execute(
                "INSERT INTO lessons (said, heard, for_ordis, ends, embedding) VALUES (?, ?, ?, ?, ?)",
                (said, heard, int(for_ordis), int(ends), vector.tobytes())
            )
            self.db.commit()

    def nearest(self, heard: str) -> list[Lesson]:
        """Past corrections closest in meaning to what the Operator just said."""

        with self.lock:
            rows = self.db.execute("SELECT said, heard, for_ordis, ends, embedding FROM lessons").fetchall()
        if not rows:
            return []

        try:
            query = embed([heard], "search_query: ")[0]
        except Exception as err:
            print(f"[Lessons] Couldn't look up corrections: {err}")
            return []

        scored = sorted(
            ((float(np.frombuffer(e, dtype=np.float32) @ query), (s, h, bool(f), bool(n))) for s, h, f, n, e in rows),
            key=lambda pair: pair[0],
            reverse=True
        )
        return [lesson for score, lesson in scored[:MAX_LESSONS] if score >= LESSON_SCORE]

    def find(self, for_ordis: bool | None = None, ends: bool | None = None) -> Lesson | None:
        """The latest decision of a kind, skipping the newest one, which is the correction being spoken right now."""

        for decision in reversed(self.recent[:-1]):
            if (for_ordis is None or decision[2] == for_ordis) and (ends is None or decision[3] == ends):
                return decision
        return None

    def not_meant_for_me(self) -> str:
        """Use when the Operator says Ordis answered something that wasn't meant for Ordis, like talk with their friends.
        Ordis learns from it so it happens less."""

        decision = self.find(for_ordis=True)
        if decision is None:
            return "There's nothing recent to learn from."

        said, heard, _, ends = decision
        self.save((said, heard, False, ends))
        return f"Learned that '{heard}' wasn't meant for Ordis."

    def was_meant_for_me(self) -> str:
        """Use when the Operator says Ordis ignored something they said to Ordis. Ordis learns from it so it happens less."""

        decision = self.find(for_ordis=False)
        if decision is None:
            return "Ordis hasn't ignored anything lately."

        said, heard, _, ends = decision
        self.save((said, heard, True, ends))
        return f"Learned that '{heard}' was meant for Ordis. It was: {heard}"

    def wasnt_finished(self) -> str:
        """Use when the Operator says Ordis stopped listening too early, as if they were done talking when they weren't.
        Ordis learns from it so it happens less."""

        decision = self.find(ends=True)
        if decision is None:
            return "Ordis hasn't ended a conversation lately."

        said, heard, for_ordis, _ = decision
        self.save((said, heard, for_ordis, False))
        return f"Learned that '{heard}' didn't mean the Operator was done."
