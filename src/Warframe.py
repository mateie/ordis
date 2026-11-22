import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

import Judge
from Memory import DB_PATH

API = "https://api.warframestat.us/pc"
CHECK_SECONDS = 300
NIGHT_MIN_MINUTES = 10

Event = tuple[str, str]


def parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def seconds_left(stamp: str) -> float:
    return (parse(stamp) - datetime.now(timezone.utc)).total_seconds()


def time_left(stamp: str) -> str:
    minutes = max(0, int(seconds_left(stamp) // 60))
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else "under a minute"


def is_active(event: dict[str, Any]) -> bool:
    now = datetime.now(timezone.utc)
    return parse(event["activation"]) <= now < parse(event["expiry"])


class Warframe:
    def __init__(self, on_alert: Callable[[str], None], model: Callable[[], str], can_judge: Callable[[], bool], num_ctx: int):
        self.on_alert = on_alert
        self.model = model
        self.can_judge = can_judge
        self.num_ctx = num_ctx

        self.announced: set[str] = set()
        self.judged: set[str] = set()

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute("CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL)")
        self.db.commit()

        self.topics = {
            "baro": self.baro,
            "cycles": self.cycles,
            "fissures": self.fissures,
            "sortie": self.sortie,
        }
        self.sources = (
            self.baro_events,
            self.night_events,
            self.fissure_events,
            self.invasion_events,
            self.steel_path_events,
        )
        self.tools = {f.__name__: f for f in (self.warframe_status, self.watch_for, self.list_watches, self.stop_watching)}

    def watch_for(self, what: str) -> str:
        """Alert the Operator whenever something they care about shows up in Warframe, like a trader arriving,
        a kind of void fissure, an invasion reward, Teshin's weekly offer or night on the Plains.

        Args:
            what: What to watch for, in the Operator's own words, like 'axi survival fissures'.
        """
        self.db.execute("INSERT INTO watches (text) VALUES (?)", (what.strip(),))
        self.db.commit()
        self.judged.clear()
        return f"Watching for: {what.strip()}."

    def list_watches(self) -> str:
        """List what Ordis is watching for in Warframe."""
        rows = self.db.execute("SELECT id, text FROM watches ORDER BY id").fetchall()
        return "\n".join(f"#{wid} {text}" for wid, text in rows) or "Not watching for anything."

    def stop_watching(self, watch_id: int) -> str:
        """Stop watching for something in Warframe. Use list_watches first to find its number.

        Args:
            watch_id: The watch's number from list_watches.
        """
        cursor = self.db.execute("DELETE FROM watches WHERE id = ?", (int(watch_id),))
        self.db.commit()
        return "Stopped watching." if cursor.rowcount else f"No watch #{watch_id}."

    def get(self, endpoint: str) -> Any:
        response = httpx.get(f"{API}/{endpoint}", params={"language": "en"}, timeout=10)
        response.raise_for_status()
        return response.json()

    def baro(self) -> str:
        trader = self.get("voidTrader")
        if not is_active(trader):
            return f"Baro Ki'Teer arrives at {trader['location']} in {time_left(trader['activation'])}."

        items = ", ".join(f"{i['item']} ({i['ducats']} ducats)" for i in trader["inventory"])
        return f"Baro Ki'Teer is at {trader['location']} for {time_left(trader['expiry'])}. Selling: {items or 'unknown'}."

    def cycles(self) -> str:
        cetus = self.get("cetusCycle")
        vallis = self.get("vallisCycle")
        cambion = self.get("cambionCycle")

        return "\n".join([
            f"Plains of Eidolon: {cetus['state']}, {time_left(cetus['expiry'])} left",
            f"Orb Vallis: {vallis['state']}, {time_left(vallis['expiry'])} left",
            f"Cambion Drift: {cambion['state']}, {time_left(cambion['expiry'])} left",
        ])

    def fissures(self) -> str:
        fissures = sorted(
            (f for f in self.get("fissures") if not f["isStorm"] and is_active(f)),
            key=lambda f: f["tierNum"]
        )

        return "\n".join(
            f"{f['tier']} {f['missionType']} on {f['node']}{' (Steel Path)' if f['isHard'] else ''}, {time_left(f['expiry'])} left"
            for f in fissures
        ) or "No fissures right now."

    def sortie(self) -> str:
        sortie = self.get("sortie")
        missions = "\n".join(f"{v['missionType']} on {v['node']}: {v['modifier']}" for v in sortie["variants"])
        return f"Today's sortie, {time_left(sortie['expiry'])} left:\n{missions}"

    def warframe_status(self, topic: str) -> str:
        """Check live Warframe game info.

        Args:
            topic: Must be exactly one of: baro (Baro Ki'Teer the Void Trader), cycles (day and night on Cetus, Fortuna and Deimos), fissures (Void Fissures for relics), sortie (today's sortie).
        """
        handler = self.topics.get(topic.lower().strip())
        if handler is None:
            return f"Unknown topic '{topic}'. Use one of: {', '.join(self.topics)}."

        try:
            return handler()
        except Exception as err:
            return f"Error: {err}"

    def baro_events(self) -> list[Event]:
        trader = self.get("voidTrader")
        if not is_active(trader):
            return []

        return [(
            f"baro-{trader['activation']}",
            f"Baro Ki'Teer the Void Trader is at {trader['location']} right now and leaves in {time_left(trader['expiry'])}."
        )]

    def night_events(self) -> list[Event]:
        cetus = self.get("cetusCycle")
        if cetus["isDay"] or seconds_left(cetus["expiry"]) < NIGHT_MIN_MINUTES * 60:
            return []

        return [(cetus["id"], f"It is night on the Plains of Eidolon, {time_left(cetus['expiry'])} left.")]

    def fissure_events(self) -> list[Event]:
        return [
            (
                f["id"],
                f"{'Steel Path ' if f['isHard'] else ''}{'Void Storm ' if f['isStorm'] else ''}{f['tier']} {f['missionType']} "
                f"void fissure on {f['node']}, {time_left(f['expiry'])} left."
            )
            for f in self.get("fissures")
            if is_active(f)
        ]

    def invasion_rewards(self, invasion: dict[str, Any]) -> list[str]:
        rewards: list[str] = []

        for side in ("attacker", "defender"):
            reward: Any = invasion[side].get("reward") or {}
            rewards += [str(item) for item in reward.get("items", [])]
            rewards += [f"{c['count']}x {c['type']}" for c in reward.get("countedItems", [])]

        return rewards

    def invasion_events(self) -> list[Event]:
        return [
            (invasion["id"], f"Invasion on {invasion['node']} rewarding {', '.join(self.invasion_rewards(invasion))}.")
            for invasion in self.get("invasions")
            if not invasion["completed"]
        ]

    def steel_path_events(self) -> list[Event]:
        steel_path = self.get("steelPath")
        return [(
            f"steelpath-{steel_path['activation']}",
            f"Teshin is offering {steel_path['currentReward']['name']} for Steel Essence this week."
        )]

    def wanted(self, watch: str, event: str) -> bool:
        """Ask the model whether an event is what the Operator asked to be alerted about."""

        return Judge.matches_watch(self.model(), self.num_ctx, watch, event)

    def check_all(self, db: sqlite3.Connection):
        if not self.can_judge():
            return

        watches = [text for (text,) in db.execute("SELECT text FROM watches").fetchall()]
        if not watches:
            return

        new: list[str] = []
        for source in self.sources:
            try:
                events = source()
            except Exception as err:
                print(f"[Warframe] {source.__name__} failed: {err}")
                continue

            for key, text in events:
                if key in self.announced or key in self.judged:
                    continue

                self.judged.add(key)
                if any(self.wanted(watch, text) for watch in watches):
                    self.announced.add(key)
                    new.append(text)

        if new:
            self.on_alert(" ".join(new))

    def loop(self, stop: threading.Event):
        db = sqlite3.connect(DB_PATH)
        try:
            while not stop.is_set():
                self.check_all(db)
                stop.wait(CHECK_SECONDS)
        finally:
            db.close()

    def start(self) -> threading.Event:
        stop = threading.Event()
        threading.Thread(target=self.loop, args=(stop,), daemon=True).start()

        return stop
