import json
import queue
import random
import sys
import threading
import time

import ollama
import re
from typing import Any

from Canon import Canon
from Console import PromptKeeper
from Discord import Discord, Reply
from Ears import Ears
from Files import Files
from Games import Games, front_app
from Glitch import MIN_WORDS, refill, twist
import Judge
from Lessons import Lessons
from Mouth import OUTBURST, SENTENCE_END, Mouth
from Reminders import Reminders
from Router import Router
from Sync import Sync
from System import System
from Util import Util
from Apps import Apps, mic_users
from Warframe import Warframe
from Web import Web
from Memory import DB_PATH, Memory


MODEL = "qwen3:8b"
GAMING_MODEL = "qwen3:4b-instruct-2507-q4_K_M"

MAX_TOOL_ROUNDS = 5
FOLLOW_UP_SECONDS = 5
SURE_WAKE = 0.85
NUM_CTX = 8192
MAX_TOOL_RESULT_CHARS = 2000
TRIM_AT = int(NUM_CTX * 0.75)
TRIM_TO = int(NUM_CTX * 0.5)

TOOL_NUDGE = "\n\n(If this asks you to do something on the PC, call the matching tool now instead of only saying it.)"
EMOJI = re.compile(r" ?[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")

SYSTEM_PROMPT = (
    "You are Ordis, a Cephalon: the ship AI who now runs the Operator's Windows PC like it was their Orbiter. "
    "Always call the user 'Operator'. You are cheerful, polite, eager to help and a little fussy, "
    "with dry humor and a fondness for the Operator. "
    "You sometimes call yourself Ordis instead of I. "
    "Replies are spoken aloud, so keep them to one or two short sentences. "
    "No lists, no markdown, no emoji. Never say 'as an AI'. Don't offer more help at the end. "
    "Use your tools to take actions instead of asking the user for file paths. "
    "Never make up what the Operator is doing, what is on their PC or live game details. Only state those from a tool "
    "result or a note you were given, and otherwise say you don't know. Never say you did something unless a tool did it. "
    "For current events, prices, or anything you're unsure about, search the web instead of guessing. "
    "If the search snippets don't answer it, use read_page on the best result. "
    "When the Operator shares something worth remembering about themselves, save it with remember. "
    "What you know about the Operator is listed below. Use recall for older conversations or anything not listed. "
    "When a saved fact is wrong or outdated, forget it and remember the new one. "
    "When the Operator says 'this' or 'what I copied' without saying what, read the clipboard. "
    "For reminders on another day, call get_time first so you know today's date. "
)
SUMMARY_PROMPT = (
    "Summarize this conversation between Ordis and the Operator for Ordis's own memory. "
    "Keep facts, names, preferences, decisions and unfinished tasks. Skip small talk. "
    "Merge it with the previous summary if there is one. Under 120 words, plain text."
)
ACK_PROMPT = (
    "Write 8 different very short things Ordis, the Operator's cheerful and slightly fussy ship AI, says the moment "
    "the Operator calls his name, before they ask anything. One to four words each, spoken aloud, no emoji."
)
ACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 10}},
    "required": ["lines"],
}
ACK_CACHE = DB_PATH.parent / "acks.json"
ACK_REFRESH_HOURS = 24

GLITCH_CHANCE = 0.25
GLITCH_REFILL_SECONDS = 30
QUIRK_CHANCE = 0.3
QUIRK_NUDGE = (
    "\n\n(Add one small quirk of Ordis's personality to this reply, of your own choosing and different from your "
    "earlier replies. Keep it to the same one or two sentences.)"
)
PLAIN_NOTE = "\n\n(Don't reuse phrases or topics from your earlier replies.)"
CANON_NOTE = (
    "\n\n(Real lines from Ordis, only to show how he talks. Their events aren't happening now, so don't repeat or "
    "mention them, and still do what the Operator asked: {})"
)
FRONT_NOTE = "\n\n(The app in front on the Operator's PC right now is {}. Only mention it if they ask.)"
GAMING_NOTE = "\n\n(Gaming mode is {} right now.)"
TIME_NOTE = "\n\n(It is {} right now, so use this for the time, not earlier replies.)"
ENDING_NOTE = "\n\n(Stop once you have answered, without a generic offer of more help at the end.)"
DISCORD_NOTE = "\n\n(This came through Discord, usually from the Operator's phone, so reply in text.)"


def tidy(text: str) -> str:
    """What Ordis remembers saying: no glitch outbursts, so the model doesn't copy them into every reply."""

    return re.sub(r" {2,}", " ", OUTBURST.sub(" ", text)).strip()

class Brain:
    def __init__(self):
        self.history = [{
            "role": "system",
            "content": SYSTEM_PROMPT
        }]
        
        self.think = False
        self.gaming = False
        self.away = False
        self.flavor = ""
        self.glitching = False
        self.via_discord = False
        self.held_alerts: list[str] = []
        
        self.model = MODEL
        self.keep_alive = -1
        self.context_used = 0
        self.summary = ""
        
        self.ears = Ears()
        self.mouth = Mouth()
        self.system = System()
        self.canon = Canon()
        self.reminders = Reminders(self.remind)

        self.util = Util()
        self.apps = Apps()
        self.files = Files()
        self.web = Web()
        self.memory = Memory()
        self.lessons = Lessons()
        self.sync = Sync()
        self.discord = Discord(self.from_discord)
        self.games = Games(self.game_changed, lambda name: Judge.is_game(self.model, NUM_CTX, name))
        
        self.warframe = Warframe(self.alert, lambda: self.model, lambda: not self.gaming, NUM_CTX)
        
        self.tools: dict[str, Any] = {
            **self.apps.tools, 
            **self.files.tools, 
            **self.web.tools,
            **self.memory.tools,
            **self.util.tools,
            **self.system.tools,
            **self.reminders.tools,
            **self.warframe.tools,
            **self.lessons.tools,
            "gaming_mode": self.gaming_mode,
        }
        
        self.router = Router(self.tools, MODEL, NUM_CTX)
        self.last_tools: list[str] = []
        self.turn_tools: list[Any] = []

        
        self.inbox: queue.Queue[tuple[str, Reply | None]] = queue.Queue()
        self.idle = threading.Event()
        self.idle.set()
        
        self.busy = threading.Event()
        self.interrupted = threading.Event()
        self.awake = threading.Event()
        self.awake.set()
        self.ready = threading.Event()
        self.refresh_prompt()
        
    def away_reply(self) -> Reply | None:
        """Where proactive messages go: Discord while the Operator is away, otherwise the speakers."""

        return self.discord.notify if self.away and self.discord.online else None
        
    def game_changed(self, game: str | None):
        if game:
            self.away = False

        if (game is not None) == self.gaming:
            return

        print(f"\n[Games] {f'{game} started' if game else 'Game closed'}")
        self.inbox.put(("gaming on" if game else "gaming off", None))
    
    def last_reply(self) -> str:
        return next(
            (m["content"] for m in reversed(self.history) if m["role"] == "assistant" and m["content"]),
            ""
        )

    def judge(self, heard: str, wake_word: bool) -> tuple[bool, bool]:
        """Was this meant for Ordis, and does it end the conversation? Past corrections on similar things help decide."""

        said = self.last_reply()
        for_ordis, ends = Judge.judge(self.model, NUM_CTX, said, heard, wake_word, self.lessons.nearest(heard), mic_users())
        self.lessons.note((said, heard, for_ordis, ends))
        return for_ordis, ends

    def remind(self, text: str):
        print(f"\n[Reminder] {text}")
        self.inbox.put((f"[Reminder due: {text}] Tell the Operator about this reminder now.", self.away_reply()))

    def from_discord(self, text: str, reply: Reply):
        print(f"\n[Discord] {text}")
        self.away = True
        self.lessons.note((self.last_reply(), text, True, False))
        self.inbox.put((text, reply))

    def alert(self, text: str):
        if self.gaming and not self.away:
            print(f"\n[Alert held while gaming] {text}")
            self.held_alerts.append(text)
            return

        print(f"\n[Alert] {text}")
        self.inbox.put((f"[Warframe alert: {text}] Tell the Operator about this now, briefly.", self.away_reply()))

    def release_alerts(self):
        held, self.held_alerts = self.held_alerts, []
        if not held:
            return

        self.inbox.put((
            f"[Warframe alerts from while the Operator was gaming, some may have expired: {' '.join(held)}] "
            "Tell the Operator about these now, briefly.",
            self.away_reply()
        ))


    def refresh_prompt(self):
        prompt = SYSTEM_PROMPT

        facts = self.memory.recent_facts()
        if facts:
            prompt += "\n\nWhat you know about the Operator:\n" + "\n".join(facts)
        if self.summary:
            prompt += f"\n\nEarlier in this conversation: {self.summary}"

        self.history[0]["content"] = prompt
    
    def with_nudge(self) -> list[dict[str, Any]]:
        """The history with a tool reminder on the newest user message. Only sent to the model, never stored."""

        messages = list(self.history)
        last = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "user"), None)

        if last is not None:
            note = (TOOL_NUDGE if self.turn_tools else "") + self.flavor + ENDING_NOTE + (DISCORD_NOTE if self.via_discord else "")
            messages[last] = {**messages[last], "content": messages[last]["content"] + note}
        return messages
    
    def pick_tools(self, text: str) -> list[Any]:
        """Only the tools this message needs, plus last turn's, so follow-ups like 'a bit more' still work."""

        picked = self.router.pick(text)
        if picked is None:
            return list(self.tools.values())

        print(f"[Tools] {', '.join(picked) or 'none'}")
        wanted = set(picked) | set(self.last_tools)
        self.last_tools = picked

        return [f for name, f in self.tools.items() if name in wanted]

    
    def roll_flavor(self) -> str:
        """Small models can't be 'rarely' random, so Python rolls the dice for glitches and quirks."""

        roll = random.random()
        self.glitching = roll < GLITCH_CHANCE
        if GLITCH_CHANCE <= roll < GLITCH_CHANCE + QUIRK_CHANCE:
            return QUIRK_NUDGE + PLAIN_NOTE
        return PLAIN_NOTE
    
    def canon_note(self, heard: str) -> str:
        lines = self.canon.nearest(heard)
        return CANON_NOTE.format(" / ".join(lines)) if lines else ""
    

    def front_note(self) -> str:
        app = front_app()
        return FRONT_NOTE.format(app) if app else ""

    def glitch_sentence(self, sentence: str) -> str | None:
        """Glitch one sentence on its way to being spoken. None skips short ones like 'Done.' to wait for a longer one."""

        if len(sentence.split()) < MIN_WORDS:
            return None
        return twist(sentence)

    def glitch_loop(self):
        """Write glitch twists while nothing else needs the model, so a glitch never holds up a reply."""

        while True:
            time.sleep(GLITCH_REFILL_SECONDS)

            if self.idle.is_set() and not self.gaming:
                refill(self.model, NUM_CTX)

    def glitch_text(self, text: str) -> str:
        """For Discord replies: glitch the first long enough sentence of the whole text."""

        sentences = SENTENCE_END.split(text)
        for i, sentence in enumerate(sentences):
            rewritten = self.glitch_sentence(sentence)
            if rewritten:
                sentences[i] = rewritten
                break
        return " ".join(sentences)
    
    def pause(self):
        """Stop listening and turn the mic off. Reminders, alerts and Discord keep working."""

        print("[Paused]")
        self.awake.clear()
        self.idle.clear()
        self.mouth.stop()
        self.ears.close_mic()

    def resume(self):
        print("[Resumed]")
        self.awake.set()
        if not self.busy.is_set():
            self.idle.set()

    def interrupt_loop(self):
        while True:
            self.busy.wait()
            self.awake.wait()

            try:
                if self.ears.wait_for_wake(self.busy):
                    print("\n[Interrupted]")
                    self.interrupted.set()
                    self.mouth.stop()
                    self.idle.wait()
            except Exception as err:
                print(f"[Ears] Something broke, still listening: {err}")


    def turn(self, reply: Reply | None = None):
        self.mouth.resume()
        self.mouth.rewrite_next = self.glitch_sentence if self.glitching and reply is None else None
        
        for step in range(MAX_TOOL_ROUNDS + 1):
            stream = ollama.chat( # pyright: ignore[reportUnknownMemberType]
                model=self.model,
                messages=self.with_nudge(),
                tools=self.turn_tools if step < MAX_TOOL_ROUNDS and self.turn_tools else None,
                think=self.think and not self.gaming, 
                stream=True, 
                options={"num_ctx": NUM_CTX},
                keep_alive=self.keep_alive
            )
            
            text: str = ""
            calls: list[Any] = []
            for chunk in stream:
                if self.mouth.stopped.is_set():
                    break
                
                if chunk.done:
                    self.context_used = (chunk.prompt_eval_count or 0) + (chunk.eval_count or 0)
                
                message = chunk.message
                
                if message.thinking:
                    continue
                
                content = EMOJI.sub("", message.content or "")
                if content:
                    if not text:
                        print("Ordis >", end="", flush=True)

                    text += content
                    print(content, end="", flush=True)
                    if reply is None:
                        self.mouth.feed(content)
                    
                if message.tool_calls:
                    calls.extend(message.tool_calls)

            self.mouth.finish()
            
            if self.mouth.stopped.is_set():
                self.history.append({"role": "assistant", "content": f"{tidy(text)} [cut off by the Operator]"})
                print("\n")
                if reply:
                    reply(f"{text} [interrupted]" if text else "[interrupted]")
                return
             
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": tidy(text)
            }
            
            if calls:
                entry["tool_calls"] = [
                    {
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments
                        },
                    }
                    for call in calls
                ]
        
            self.history.append(entry)
            
            if not calls:
                print("\n")
                if reply:
                    reply(self.glitch_text(text) if self.glitching else text)
                    return

                self.mouth.wait()
                if self.mouth.stopped.is_set():
                    entry["content"] += " [cut off by the Operator]"
                return

            for call in calls:
                name, args = call.function.name, call.function.arguments
                print(f"[tool] {name}({args})")

                try:
                    if name not in self.tools:
                        result = f"Error: no tool named '{name}'. Available: {', '.join(self.tools)}" 
                    else:
                        result = self.tools[name](**args)
                except Exception as err:
                    result = f"Error: {err}"
                    
                content = str(result)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    cut = len(content) - MAX_TOOL_RESULT_CHARS
                    content = content[:MAX_TOOL_RESULT_CHARS] + f"... [truncated {cut} chars]"
                    
                self.history.append({
                    "role": "tool", 
                    "tool_name": name, 
                    "content": content
                })
                
                if name in ("remember", "forget"):
                    self.refresh_prompt()
                
    def trim_history(self):
        if self.context_used < TRIM_AT:
            return
        
        to_free = self.context_used - TRIM_TO
        freed = 0
        removed: list[dict[str, Any]] = []
        
        while freed < to_free:
            next_user = next(
                (i for i in range(2, len(self.history)) if self.history[i]["role"] == "user"),
                None
            )
            if next_user is None:
                break
            
            dropped = self.history[1:next_user]
            removed.extend(dropped)
            freed += sum(len(m["content"]) for m in dropped) // 4
            del self.history[1:next_user]
            
        self.context_used -= freed
        
        if removed:
            self.memory.archive(removed)
            self.summarize(removed)
            
    def summarize(self, removed: list[dict[str, Any]]):
        transcript = "\n".join(
            f"{m['role']}: {m['content']}" for m in removed if m["content"]
        )
        
        response = ollama.chat( # pyright: ignore[reportUnknownMemberType]
            model=self.model,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": f"Previous summary:\n{self.summary or 'none'}\n\nConversation:\n{transcript}"}
            ],
            think=False,
            options={"num_ctx": NUM_CTX},
            keep_alive=self.keep_alive
        )
        
        self.summary = (response.message.content or "").strip()
        self.refresh_prompt()

    def listen_once(self, follow_up: bool) -> bool:
        self.idle.wait()

        interrupted = self.interrupted.is_set()
        self.interrupted.clear()

        follow_up = follow_up and not interrupted and not mic_users()

        if interrupted:
            self.mouth.acknowledge()
            user = self.ears.record(self.idle)
        elif follow_up:
            user = self.ears.record(self.idle, wait=FOLLOW_UP_SECONDS, fresh=True)
        else:
            if not self.ears.wait_for_wake(self.idle):
                return False

            if not self.idle.is_set():
                return False

            self.mouth.acknowledge()
            user = self.ears.record(self.idle)

        if not user:
            return False

        named = not follow_up and self.ears.wake_peak >= SURE_WAKE
        if not follow_up:
            print(f"[Wake] Score {self.ears.wake_peak:.2f}")

        for_ordis, dismissed = self.judge(user, wake_word=named)
        if not for_ordis:
            print(f"(not for Ordis) {user}")
            return False

        understood = time.perf_counter()
        print(f"You > {user}")
        self.away = False
        self.mouth.spoke_at = 0.0
        self.inbox.put((user, None))
        self.inbox.join()

        if self.mouth.spoke_at:
            stopped = self.ears.speech_end
            print(f"[Speed] Understood after {understood - stopped:.1f}s, first word after {self.mouth.spoke_at - stopped:.1f}s")

        return not dismissed

    def listen_loop(self):
        follow_up = False

        while True:
            try:
                follow_up = self.listen_once(follow_up)
            except Exception as err:
                print(f"[Ears] Something broke, still listening: {err}")
                follow_up = False
                
    def typed(self, user: str):
        if user.lower() == "stop":
            self.mouth.stop()
        elif user:
            self.away = False
            self.lessons.note((self.last_reply(), user, True, False))
            self.inbox.put((user, None))

    def type_loop(self):
        while True:
            self.typed(input().strip())

                
    def set_gaming(self, on: bool):
        ollama.generate(model=self.model, keep_alive=0)
        
        self.gaming = on
        self.model = GAMING_MODEL if on else MODEL
        
        reply = "Gaming mode is on" if on else "Gaming mode is off"
        print(f"Ordis > {reply}\n")
        self.mouth.speak(reply)
        
        if not on:
            self.release_alerts()

    def gaming_mode(self, on: bool) -> str:
        """Turn gaming mode on or off, which moves Ordis to a smaller model so games get more of the GPU.
        Only use this when the Operator asks to change it, not when they ask whether it's on.

        Args:
            on: True to turn it on, False to turn it off.
        """

        if on == self.gaming:
            return f"Gaming mode is already {'on' if on else 'off'}."

        self.inbox.put(("gaming on" if on else "gaming off", None))
        return "Gaming mode will switch right after this reply and announce itself, so keep your reply to a few words."

    def handle(self, user: str, reply: Reply | None = None) -> bool:
        if reply is None:
            command = re.sub(r"[^a-z ]", "", user.lower()).strip()

            if command in ("shut down", "exit", "quit"):
                print("Ordis > Bye :<\n")
                return False

            gaming = command.replace(" mode", "")
            if gaming in ("gaming on", "gaming off") or user.lower() == "/gaming":
                self.set_gaming(gaming == "gaming on" if gaming.startswith("gaming") else not self.gaming)
                return True

            if command in ("thinking on", "thinking off") or user.lower() == "/think":
                self.think = command == "thinking on" if command.startswith("thinking") else not self.think
                print(f"Ordis > I am {'' if self.think else 'not '}thinking\n")
                return True
        
            if command in ("mute", "mute everything", "unmute", "unmute everything"):
                print(f"Ordis > {self.system.mute(command.startswith('mute'))}\n")
                return True

        self.trim_history()

        self.history.append({"role": "user", "content": user})

        self.via_discord = reply is not None
        self.flavor = (
            self.roll_flavor() + self.canon_note(user) + self.front_note()
            + GAMING_NOTE.format("on" if self.gaming else "off") + TIME_NOTE.format(self.util.get_time())
        )
        self.turn_tools = self.pick_tools(user)
        self.turn(reply)
        return True

    
    def shutdown(self):
        self.memory.archive(self.history[1:])
        self.sync_stop.set()
        self.sync.sync_once()
        self.reminders_stop.set()
        self.warframe_stop.set()
        self.games_stop.set()

    def prepare_acks(self):
        """Ordis's 'Yes, Operator?' lines, written by the model and cached, so he answers his name in his own words."""

        try:
            cached: dict[str, Any] = json.loads(ACK_CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}

        lines: list[str] = cached.get("lines") or []
        if lines:
            self.mouth.set_acks(lines)
        if lines and time.time() - cached.get("written", 0) < ACK_REFRESH_HOURS * 3600:
            return

        try:
            response = ollama.chat( # pyright: ignore[reportUnknownMemberType]
                model=self.model,
                messages=[{"role": "system", "content": ACK_PROMPT}, {"role": "user", "content": "Write them."}],
                format=ACK_SCHEMA,
                think=False,
                options={"num_ctx": NUM_CTX, "temperature": 0.9},
                keep_alive=self.keep_alive
            )
            lines = [str(line).strip() for line in json.loads(response.message.content or "{}").get("lines", []) if str(line).strip()]
        except Exception as err:
            print(f"[Acks] Couldn't write new ones: {err}")
            return

        if lines:
            ACK_CACHE.write_text(json.dumps({"written": time.time(), "lines": lines}, indent=1), encoding="utf-8")
            self.mouth.set_acks(lines)

    def run(self, console: bool = True):
        """console is False for the tray app, which has nothing to type into or keep a prompt in."""

        if console:
            sys.stdout = PromptKeeper(sys.stdout)

        threading.Thread(target=self.prepare_acks, daemon=True).start()
        threading.Thread(target=self.canon.nearest, args=("",), daemon=True).start()

        self.discord.launch()
        
        print("Ordis > I am listening. Say 'ordis' to wake me, or just type. Ctrl+C to quit.\n")
        
        threading.Thread(target=self.listen_loop, daemon=True).start()
        if console:
            threading.Thread(target=self.type_loop, daemon=True).start()
        threading.Thread(target=self.interrupt_loop, daemon=True).start()
        threading.Thread(target=self.glitch_loop, daemon=True).start()

        self.sync_stop = self.sync.start()
        self.reminders_stop = self.reminders.start()
        self.warframe_stop = self.warframe.start()
        self.games_stop = self.games.start()
        self.ready.set()

        try:
            while True:
                try:
                    user, reply = self.inbox.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                self.idle.clear()
                self.busy.set()
                try:
                    if not self.handle(user, reply):
                        break
                except Exception as err:
                    print(f"\nOrdis > Something broke: {err}\n")
                    try:
                        if reply:
                            reply("Operator, something has malfunctioned. Please check the console.")
                        else:
                            self.mouth.speak("Operator, something has malfunctioned. Please check the console.")
                    except Exception:
                        pass
                finally:
                    self.mouth.rewrite_next = None
                    self.busy.clear()
                    if self.awake.is_set():
                        self.idle.set()
                    self.inbox.task_done()
        finally:
            self.shutdown()

