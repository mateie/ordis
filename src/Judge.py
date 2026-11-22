import json
from typing import Any

import ollama

JUDGE_PROMPT = (
    "Ordis is the Operator's voice assistant on their PC. Judge what the Operator just said, given what Ordis said "
    "right before, and answer two things. "
    "for_ordis: was it meant for Ordis? If they said Ordis's name, yes. Otherwise they may be in a voice call with "
    "friends while gaming. A request a PC voice assistant could carry out, like controlling apps, sound, files, "
    "reminders or looking something up, is for Ordis whatever the topic. If Ordis just asked a question, any answer "
    "to it is for Ordis, even if it sounds like small talk. A short reaction to Ordis's answer, or asking more about "
    "what Ordis just said, even with words like he, it or that, is also for Ordis. "
    "Anything else is not: talking to friends, reacting to a game, making plans, or asking people something. "
    "ends_conversation: are they done and want nothing more right now, such as saying goodbye, saying they need "
    "nothing more, dropping their request, or thanking Ordis without asking for anything new? Not if it asks a question, "
    "makes a request, greets Ordis or keeps chatting, even with thanks. If Ordis just asked a question, answering it "
    "does not end the conversation."
)
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"for_ordis": {"type": "boolean"}, "ends_conversation": {"type": "boolean"}},
    "required": ["for_ordis", "ends_conversation"],
}
IN_CALL = (
    "{apps} also using the microphone right now, so the Operator may be in a voice call. Lines that answer, thank or "
    "react to what Ordis just said are still for Ordis; other lines are more likely for the call."
)
NO_CALL = "No other app is using the microphone right now, so the Operator isn't in a voice call."
WATCH_PROMPT = (
    "The Operator asked Ordis to alert them about something in Warframe. Decide if this event is exactly what they asked about. "
    "Every detail they gave must match the event, like the relic tier, mission type, reward, trader or time of day. "
    "If any detail they asked for is different or missing from the event, answer no. Answer with only yes or no."
)
GAME_PROMPT = "Is this Windows program name a video game? Answer with only yes or no."
Lesson = tuple[str, str, bool, bool]


def chat(model: str, num_ctx: int, messages: list[dict[str, str]], **extra: Any) -> str | None:
    """One short, deterministic side question to the model. None if it couldn't answer."""

    try:
        response = ollama.chat( # type: ignore
            model=model,
            messages=messages,
            think=False,
            options={"num_ctx": num_ctx, "temperature": 0, "num_predict": 40},
            keep_alive=-1,
            **extra
        )
    except Exception as err:
        print(f"[Judge] Check failed: {err}")
        return None
    return response.message.content or "" # type: ignore


def yes_no(model: str, num_ctx: int, prompt: str, content: str) -> bool | None:
    reply = chat(model, num_ctx, [{"role": "system", "content": prompt}, {"role": "user", "content": content}])
    return None if reply is None else reply.strip().lower().startswith("yes")


def situation(said: str, heard: str, wake_word: bool, mic: list[str] | None = None) -> str:
    """mic is the other apps using the microphone, or None when that isn't known, like for past corrections."""

    named = "" if wake_word else ", without saying Ordis's name"
    text = f"Ordis just said: \"{said}\"\nThen the Operator said{named}: \"{heard}\""
    if mic is None:
        return text
    room = IN_CALL.format(apps=f"{', '.join(mic)} {'is' if len(mic) == 1 else 'are'}") if mic else NO_CALL
    return f"{room}\n{text}"


def judge(
    model: str, num_ctx: int, said: str, heard: str, wake_word: bool,
    lessons: list[Lesson] | None = None, mic: list[str] | None = None
) -> tuple[bool, bool]:
    """Was this meant for Ordis, and does it end the conversation? One call answers both.
    Lessons are the Operator's past corrections on similar things, shown to the model as examples.
    mic is the other apps using the microphone, since a voice call changes who the Operator is likely talking to."""

    messages = [{"role": "system", "content": JUDGE_PROMPT}]
    for past_said, past_heard, for_ordis, ends in lessons or []:
        messages += [
            {"role": "user", "content": situation(past_said, past_heard, False)},
            {"role": "assistant", "content": json.dumps({"for_ordis": for_ordis, "ends_conversation": ends})},
        ]
    messages.append({"role": "user", "content": situation(said, heard, wake_word, mic)})

    reply = chat(model, num_ctx, messages, format=JUDGE_SCHEMA)
    try:
        answer: dict[str, Any] = json.loads(reply or "")
    except json.JSONDecodeError:
        return True, False

    return wake_word or bool(answer.get("for_ordis", True)), bool(answer.get("ends_conversation", False))


def is_game(model: str, num_ctx: int, program: str) -> bool:
    """Can't tell counts as a game, so a failed check doesn't stop gaming mode."""

    return yes_no(model, num_ctx, GAME_PROMPT, program) is not False


def matches_watch(model: str, num_ctx: int, watch: str, event: str) -> bool:
    return yes_no(model, num_ctx, WATCH_PROMPT, f"Asked about: {watch}\nEvent: {event}") is True
