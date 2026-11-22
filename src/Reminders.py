from datetime import datetime, timedelta
import sqlite3
import threading
from typing import Callable

from Memory import DB_PATH


CHECK_SECONDS = 15
TIME_FORMATS = ("%Y-%m-%d %H:%M", "%H:%M")
SPOKEN_TIME = "%A %I:%M %p"

class Reminders:
    def __init__(self, on_due: Callable[[str], None]):
        self.on_due = on_due
        
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS reminders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "text TEXT NOT NULL, "
            "due TEXT NOT NULL, "
            "done INTEGER NOT NULL DEFAULT 0)"
        )
        self.db.commit()

        self.tools = {f.__name__: f for f in (self.set_reminder, self.list_reminders, self.cancel_reminder,)}
        
    def parse_time(self, at: str) -> datetime:
        now = datetime.now()
        
        for fmt in TIME_FORMATS:
            try:
                when = datetime.strptime(at.strip(), fmt)
            except ValueError:
                continue
            
            if fmt == "%H:%M":
                when = now.replace(hour=when.hour, minute=when.minute, second=0, microsecond=0)
                if when <= now:
                    when += timedelta(days=1)
                    
            return when

        raise ValueError(f"Can't read time '{at}'. Use HH:MM or YYYY-MM-DD HH:MM.")
    
    def set_reminder(self, text: str, minutes: int = 0, at: str = "") -> str:
        """Remind the Operator about something later. Give either minutes or at, not both.

        Args:
            text: What to remind them about, like 'take the pizza out'.
            minutes: Minutes from now, for 'in 20 minutes' (20) or 'in 2 hours' (120).
            at: A clock time in 24-hour format like '17:30', or with a date like '2026-11-02 09:00'.
        """
        try:
            if at:
                due = self.parse_time(at)
            elif int(minutes) > 0:
                due = datetime.now() + timedelta(minutes=int(minutes))
            else:
                return "Error: give either minutes or at."
            
            self.db.execute(
                "INSERT INTO reminders (text, due) VALUES (?, ?)",
                (text, due.isoformat(timespec="seconds"))
            )
            
            self.db.commit()
            
            return f"Reminder set for {due:{SPOKEN_TIME}}."
        except Exception as err:
            return f"Error: {err}"
        
    
    def list_reminders(self) -> str:
        """List the Operator's upcoming reminders."""
        rows = self.db.execute(
            "SELECT id, text, due FROM reminders WHERE done = 0 ORDER BY due"
        ).fetchall()

        if not rows:
            return "No upcoming reminders."

        return "\n".join(
            f"#{rid} {datetime.fromisoformat(due):{SPOKEN_TIME}}: {text}" for rid, text, due in rows
        )
        
    def cancel_reminder(self, reminder_id: int) -> str:
        """Cancel an upcoming reminder. Use list_reminders first to find its number.

        Args:
            reminder_id: The reminder's number from list_reminders.
        """
        cursor = self.db.execute(
            "UPDATE reminders SET done = 1 WHERE id = ? AND done = 0", (int(reminder_id),)
        )
        self.db.commit()
        return "Cancelled." if cursor.rowcount else f"No upcoming reminder #{reminder_id}."

    def loop(self, stop: threading.Event):
        db = sqlite3.connect(DB_PATH)

        try:
            while not stop.is_set():
                now = datetime.now().isoformat(timespec="seconds")
                rows = db.execute(
                    "SELECT id, text FROM reminders WHERE done = 0 AND due <= ?", (now,)
                ).fetchall()

                for rid, text in rows:
                    db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
                    db.commit()
                    self.on_due(text)

                stop.wait(CHECK_SECONDS)
        finally:
            db.close()

    def start(self) -> threading.Event:
        stop = threading.Event()
        threading.Thread(target=self.loop, args=(stop,), daemon=True).start()

        return stop
            
