import os
import sqlite3
import threading


import psycopg
from dotenv import load_dotenv

from Memory import DB_PATH

load_dotenv()

TABLES = ("facts", "messages")
SYNC_SECONDS = 300

class Sync:
    def __init__(self):
        self.url = os.environ.get("DB_URL")
        
    def loop(self, stop: threading.Event):
        while not stop.is_set():
            self.sync_once()
            stop.wait(SYNC_SECONDS)

    
    def start(self) -> threading.Event:
        stop = threading.Event()
        threading.Thread(target=self.loop, args=(stop,), daemon=True).start()
        
        return stop
        
    def push(self, local: sqlite3.Connection, remote: psycopg.Connection) -> int:
        pushed = 0
        
        for table in TABLES:
            rows = local.execute(
                f"SELECT id, text, created FROM {table} WHERE synced = 0"
            ).fetchall()
            
            if not rows:
                continue
            
            with remote.cursor() as cursor:
                cursor.executemany(
                    f"INSERT INTO {table} (id, text, created) VALUES (%s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    rows
                )
                
            remote.commit()
            
            local.executemany(
                f"UPDATE {table} SET synced = 1 WHERE id = ?",
                [(row[0],) for row in rows]
            )
            
            local.commit()
            
            pushed += len(rows)
            
        return pushed
    
    def setup(self, local: sqlite3.Connection):
        local.execute(
            "CREATE TABLE IF NOT EXISTS sync_state ("
            "name TEXT PRIMARY KEY, "
            "last_seq INTEGER NOT NULL)"
        )
        
        local.commit()
        
        
    def pull(self, local: sqlite3.Connection, remote: psycopg.Connection) -> int:
        pulled = 0
        
        for table in TABLES:
            row = local.execute(
                "SELECT last_seq FROM sync_state WHERE name = ?", (table,)
            ).fetchone()
            
            last_seq = row[0] if row else 0
            
            rows = remote.execute(
                f"SELECT seq, id, text, created from {table} WHERE seq > %s ORDER BY seq",
                (last_seq,)
            ).fetchall()
            
            if not rows:
                continue
            
            cursor = local.executemany(
                f"INSERT INTO {table} (id, text, created, synced) VALUES (?, ?, ?, 1) "
                "ON CONFLICT (id) DO NOTHING",
                [(id, text, created) for _, id, text, created in rows]
            )
            local.execute(
                "INSERT INTO sync_state (name, last_seq) VALUES (?, ?) "
                "ON CONFLICT (name) DO UPDATE SET last_seq = excluded.last_seq",
                (table, rows[-1][0])
            )
            local.commit()
            
            pulled += cursor.rowcount
            
        return pulled
    
    def sync_once(self):
        if not self.url:
            return
        
        local = sqlite3.connect(DB_PATH)
        try:
            self.setup(local)
            with psycopg.connect(self.url, connect_timeout=5) as remote:
                pushed = self.push(local, remote)
                pulled = self.pull(local, remote)
                if pushed or pulled:
                    print(f"[Sync] Pushed {pushed}, pulled {pulled}")
        except Exception as err:
            print(f"[Sync] Failed: {err}")
        finally:
            local.close()