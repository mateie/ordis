import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import ollama
from numpy.typing import NDArray

DB_PATH = Path(os.environ["LOCALAPPDATA"]) / "Ordis" / "memory.db"

TABLES = ("facts", "messages")
EMBED_MODEL = "nomic-embed-text"
EMBED_OPTIONS = {"num_gpu": 0}
EMBED_BATCH = 64
EMBED_ATTEMPTS = 3
EMBED_RETRY_SECONDS = 1
MIN_SCORE = 0.5
PROMPT_FACTS = 30

embed_lock = threading.Lock()


def embed_batch(inputs: list[str]) -> Sequence[Sequence[float]]:
    """One batch at a time across every thread. Ollama opens a connection per text, so a big burst can get refused for a moment."""

    for _ in range(EMBED_ATTEMPTS - 1):
        try:
            with embed_lock:
                return ollama.embed(model=EMBED_MODEL, input=inputs, options=EMBED_OPTIONS, keep_alive=-1).embeddings
        except ollama.ResponseError:
            time.sleep(EMBED_RETRY_SECONDS)

    with embed_lock:
        return ollama.embed(model=EMBED_MODEL, input=inputs, options=EMBED_OPTIONS, keep_alive=-1).embeddings


def embed(texts: list[str], prefix: str) -> NDArray[np.float32]:
    """Turn texts into unit-length vectors, so a dot product between two is their similarity."""

    inputs = [prefix + t for t in texts]
    rows = [row for start in range(0, len(inputs), EMBED_BATCH) for row in embed_batch(inputs[start:start + EMBED_BATCH])]
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class Memory:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            "id TEXT PRIMARY KEY, "
            "text TEXT NOT NULL, "
            "created TEXT DEFAULT CURRENT_TIMESTAMP, "
            "synced INTEGER NOT NULL DEFAULT 0)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id TEXT PRIMARY KEY, "
            "text TEXT NOT NULL, "
            "created TEXT DEFAULT CURRENT_TIMESTAMP, "
            "synced INTEGER NOT NULL DEFAULT 0)"
        )

        for table in TABLES:
            columns = [row[1] for row in self.db.execute(f"PRAGMA table_info({table})")]
            if "embedding" not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN embedding BLOB")

        self.db.commit()
        
        self.tools = {f.__name__: f for f in (self.remember, self.recall, self.forget)}

    def remember(self, fact: str) -> str:
        """Save a lasting fact about the Operator, like preferences, people, hardware or plans.

        Args:
            fact: One short standalone sentence, like 'The Operator's GPU is an RTX 4070.'
        """
        self.db.execute(
            "INSERT INTO facts (id, text) VALUES (?, ?)",
            (str(uuid.uuid7()), fact)
        )

        self.db.commit()
        self.backfill("facts")
        return "Saved."

    def forget(self, fact: str) -> str:
        """Delete a saved fact that is wrong or outdated. When a fact changed, forget the old one and remember the new one.

        Args:
            fact: A few words from the saved fact, like 'RTX 4070'.
        """
        rows = self.db.execute(
            "SELECT id, text FROM facts WHERE LOWER(text) LIKE ?",
            (f"%{fact.lower().strip()}%",)
        ).fetchall()

        if not rows:
            return "No saved fact matches that."
        if len(rows) > 1:
            return "Several facts match, use more exact words:\n" + "\n".join(text for _, text in rows)

        self.db.execute("DELETE FROM facts WHERE id = ?", (rows[0][0],))
        self.db.commit()
        return f"Forgot: {rows[0][1]}"
    
    def archive(self, messages: list[dict[str, Any]]):
        speakers = {
            "user": "Operator",
            "assistant": "Ordis"
        }
        
        self.db.executemany(
            "INSERT INTO messages (id, text) VALUES (?, ?)",
            [(str(uuid.uuid7()), f"{speakers[m['role']]}: {m['content']}") for m in messages
            if m["role"] in speakers and m["content"]]
        )

        self.db.commit()
        self.backfill("messages")

    def backfill(self, table: str):
        """Embed rows that don't have a vector yet: new ones, and ones Sync pulled from another PC."""

        rows = self.db.execute(f"SELECT id, text FROM {table} WHERE embedding IS NULL").fetchall()

        for start in range(0, len(rows), EMBED_BATCH):
            batch = rows[start:start + EMBED_BATCH]
            try:
                vectors = embed([text for _, text in batch], "search_document: ")
            except Exception as err:
                print(f"[Memory] Couldn't embed {table}: {err}")
                return

            self.db.executemany(
                f"UPDATE {table} SET embedding = ? WHERE id = ?",
                [(vector.tobytes(), rid) for vector, (rid, _) in zip(vectors, batch)]
            )
            self.db.commit()

    def recent_facts(self, limit: int = PROMPT_FACTS) -> list[str]:
        rows = self.db.execute(
            "SELECT text, created FROM facts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

        return [f"[{created[:10]}] {text}" for text, created in reversed(rows)]
        
    def keyword_search(self, table: str, words: list[str], limit: int) -> list[tuple[str, str]]:
        if not words:
            return []

        where = " OR ".join("LOWER(text) LIKE ?" for _ in words)
        
        return self.db.execute(
            f"SELECT text, created FROM {table} where {where} ORDER BY id DESC LIMIT {limit}",
            [f"%{w}%" for w in words]
        ).fetchall()

    def semantic_search(self, table: str, query: NDArray[np.float32], limit: int) -> list[tuple[str, str]]:
        rows = self.db.execute(
            f"SELECT text, created, embedding FROM {table} WHERE embedding IS NOT NULL"
        ).fetchall()

        if not rows:
            return []

        matrix = np.stack([np.frombuffer(e, dtype=np.float32) for _, _, e in rows])
        scores = matrix @ query
        best = np.argsort(-scores)[:limit]

        return [(rows[i][0], rows[i][1]) for i in best if scores[i] >= MIN_SCORE]
    
    def recall(self, query: str) -> str:
        """Search saved memories and past conversations with the Operator.

        Args:
            query: What you're looking for, like 'graphics card' or 'sister birthday'.
        """
        try:
            for table in TABLES:
                self.backfill(table)

            vector = embed([query], "search_query: ")[0]
            facts = self.semantic_search("facts", vector, 5)
            past = self.semantic_search("messages", vector, 5)
        except Exception as err:
            print(f"[Memory] Semantic search failed, using keywords: {err}")
            words = [w for w in query.lower().split() if len(w) > 2]
            facts = self.keyword_search("facts", words, 10)
            past = self.keyword_search("messages", words, 5)
        
        if not facts and not past:
            return "No matching memories."
        
        parts: list[str] = []
        
        if facts:
            parts.append("Facts:\n" + "\n".join(f"[{c[:10]}] {t}" for t, c in facts))
        if past:
            parts.append("Past conversations:\n" + "\n".join(f"[{c[:10]}] {t}" for t, c in past))

        return "\n\n".join(parts)
