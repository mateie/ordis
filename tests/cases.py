"""Test data for the scorecard. Only used to grade Ordis, never to make his decisions."""

DONE = "Done, Operator. Game volume is now 40 percent."
ASKED = "I'm doing splendidly, Operator! How about you?"
BARO = "Baro Ki'Teer arrives at Larunda Relay in one day, Operator."
VOLUME = "Done, Operator. Volume is now 90%."

# (what Ordis said, what the Operator said, said Ordis's name?, meant for Ordis?, ends the conversation?)
JUDGE = [
    # Follow-ups meant for Ordis
    (DONE, "a bit lower", False, True, False),
    (DONE, "thanks", False, True, True),
    (DONE, "now mute chat", False, True, False),
    (ASKED, "I'm good, thank you", False, True, False),
    (ASKED, "pretty tired honestly", False, True, False),
    (BARO, "what is he selling", False, True, False),
    (BARO, "nice", False, True, None),
    (DONE, "open spotify", False, True, False),
    (DONE, "remind me to call mom at 6", False, True, False),
    (BARO, "and when does he leave", False, True, False),
    # Friends in a voice call
    (DONE, "can you guys hear me", False, False, None),
    (DONE, "which map are we playing", False, False, None),
    (DONE, "yeah I'm down", False, False, None),
    (DONE, "wait what did he say", False, False, None),
    (DONE, "I'll be back in like five minutes", False, False, None),
    (DONE, "lmao no way", False, False, None),
    (DONE, "are you ready", False, False, None),
    (DONE, "let me grab a drink real quick", False, False, None),
    (ASKED, "bro that was insane", False, False, None),
    (BARO, "who's carrying tonight", False, False, None),
    (DONE, "did you get the drop", False, False, None),
    (DONE, "I'm gonna queue up again", False, False, None),
    (DONE, "sorry my mic was muted", False, False, None),
    (BARO, "what are you guys doing later", False, False, None),
    # Ending the conversation, after saying Ordis's name
    (VOLUME, "Goodbye Ordis", True, True, True),
    (VOLUME, "thanks, that's all", True, True, True),
    (VOLUME, "never mind", True, True, True),
    (VOLUME, "that's it for now", True, True, True),
    (VOLUME, "nothing, I'm good", True, True, True),
    (VOLUME, "bye", True, True, True),
    (VOLUME, "thanks, now turn the volume down", True, True, False),
    (VOLUME, "what time is it", True, True, False),
    (VOLUME, "thanks! what time is it though", True, True, False),
    (VOLUME, "good morning Ordis", True, True, False),
    (VOLUME, "how are you doing", True, True, False),
    (ASKED, "good, thanks. bye", True, True, True),
    (VOLUME, "never mind the volume, open Spotify instead", True, True, False),
]

# A correction the Operator makes, then a different but similar thing they say later that it should help with:
# ((what Ordis said, what the Operator said, for Ordis, ends), (what Ordis said, what the Operator says later, for Ordis))
LEARN = [
    ((ASKED, "doing well, thanks for asking", True, False), (ASKED, "I'm good, thank you", True)),
    ((DONE, "you guys ready?", False, False), (DONE, "are you ready", False)),
    ((DONE, "brb grabbing some water", False, False), (DONE, "let me grab a drink real quick", False)),
    ((BARO, "that's sick", True, False), (BARO, "nice", True)),
    ((DONE, "dude did you see that", False, False), (DONE, "wait what did he say", False)),
]

# (what the Operator asked to watch for, a Warframe event, should it alert?)
WATCH = [
    ("axi survival fissures", "Axi Survival void fissure on Nimus (Eris), 40m left.", True),
    ("axi survival fissures", "Steel Path Axi Survival void fissure on Apollo (Lua), 1h left.", True),
    ("axi survival fissures", "Steel Path Lith Rescue void fissure on Pacific (Earth), 20m left.", False),
    ("axi survival fissures", "Axi Mobile Defense void fissure on Aten (Void), 47m left.", False),
    ("axi survival fissures", "Neo Survival void fissure on Hydron (Sedna), 30m left.", False),
    ("when baro is here", "Baro Ki'Teer the Void Trader is at Larunda Relay (Mercury) right now and leaves in 1d 2h.", True),
    ("when baro is here", "It is night on the Plains of Eidolon, 40m left.", False),
    ("invasions with orokin catalysts", "Invasion on Kiliken (Venus) rewarding 1x Orokin Catalyst Blueprint.", True),
    ("invasions with orokin catalysts", "Invasion on Ares (Mars) rewarding 3x Detonite Injector.", False),
    ("invasions with orokin catalysts", "Invasion on Calypso (Saturn) rewarding 3x Fieldron, 3x Detonite Injector.", False),
    ("night on the plains", "It is night on the Plains of Eidolon, 40m left.", True),
    ("night on the plains", "Teshin is offering Rifle Riven Mod for Steel Essence this week.", False),
    ("riven mods from teshin", "Teshin is offering Rifle Riven Mod for Steel Essence this week.", True),
    ("steel path fissures", "Steel Path Neo Defense void fissure on Stephano (Uranus), 30m left.", True),
    ("steel path fissures", "Lith Sabotage void fissure on Cervantes (Earth), 10m left.", False),
]

# Sentences Ordis might say, to see how often a glitch comes out
GLITCH = [
    "Opening Spotify now, Operator.",
    "I'll remind you to stretch in twenty minutes, Operator.",
    "The ship's systems are running smoothly, Operator.",
    "I have tidied your desktop and sorted every file into folders.",
    "A warm bowl of soup would suit you nicely, Operator.",
    "Game volume is now at forty percent, Operator.",
    "Your friends are waiting for you in the Discord call, Operator.",
    "I made you a fresh playlist full of your favorite songs.",
]

# What the Operator says, and the kind of tool it needs (None for small talk)
ROUTE = {
    "audio": ["turn the game down a bit", "mute media please", "skip this song", "pause the music", "set the volume to 30",
              "a bit louder", "unmute discord", "chat to 60 percent", "go back a song", "it's too loud",
              "can you turn discord down", "play the next one", "quieter please", "kill the music", "make my friends louder"],
    "apps": ["open spotify for me", "close discord", "launch warframe", "start chrome", "quit steam", "fire up spotify",
             "shut down discord", "get steam running"],
    "files": ["add eggs to my shopping list", "what's in my notes folder", "write down a note about the meeting",
              "what files are on my desktop", "read my todo list"],
    "web": ["search for rtx 5070 price", "what's the weather tomorrow in tbilisi", "who won the champions league",
            "look up how to cook rice", "any news about warframe's next update", "google the best warframe builds",
            "is it going to rain later", "how much is a ps5 right now", "google cheap flights to rome"],
    "memory": ["remember my sister's birthday is march 3", "what gpu do I have", "do you remember what I said about my trip",
               "forget that I have a 4070", "my favorite food is khachapuri", "I just moved to berlin", "my cat's name is nova",
               "what's my sister's name"],
    "clipboard": ["summarize what I copied", "translate this", "copy that to my clipboard", "what did I just copy",
                  "read what's on my clipboard"],
    "system": ["how's the ship doing", "how hot is my gpu", "how much space is on my c drive", "lock my pc",
               "take a screenshot of my right screen", "is my computer overheating", "am I running out of storage",
               "snap my left monitor"],
    "reminders": ["remind me in 20 minutes to stretch", "what reminders do I have", "cancel my 5pm reminder",
                  "set a timer for 10 minutes", "what time is it", "remind me tomorrow at 9 to call mom",
                  "wake me up in an hour", "don't let me forget the oven in 15 minutes", "what day is it"],
    "warframe": ["when is baro coming", "is it night on cetus", "any axi fissures right now", "what's today's sortie",
                 "what is baro selling", "has the void trader arrived", "eidolon hunt tonight?",
                 "what relics can I crack right now", "when's the next eidolon night", "what fissures are up"],
    None: ["how are you", "tell me a joke", "thanks", "good morning ordis", "tell me a fun fact", "I'm bored", "nice"],
}
# Which tools count as right for each kind above
ROUTE_TOOLS = {
    "audio": {"media", "set_volume", "channel_volume", "channel_mute", "mute"},
    "apps": {"open_app", "close_app"},
    "files": {"list_files", "read_file", "write_file", "append_file"},
    "web": {"search_web", "read_page"},
    "memory": {"remember", "recall", "forget"},
    "clipboard": {"read_clipboard", "copy_to_clipboard"},
    "system": {"system_status", "lock_pc", "screenshot"},
    "reminders": {"set_reminder", "list_reminders", "cancel_reminder", "get_time"},
    "warframe": {"warframe_status", "watch_for", "list_watches", "stop_watching"},
}
