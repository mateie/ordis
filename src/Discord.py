import asyncio
import os
import threading
from typing import Any, Callable, Coroutine

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

MAX_MESSAGE = 2000

Reply = Callable[[str], None]
Send = Callable[[str], Coroutine[Any, Any, Any]]


def chunks(text: str) -> list[str]:
    return [text[i:i + MAX_MESSAGE] for i in range(0, len(text), MAX_MESSAGE)] or ["..."]


class Discord(discord.Client):
    def __init__(self, forward: Callable[[str, Reply], None]):
        super().__init__(intents=discord.Intents.default())

        self.forward = forward
        self.token = os.environ.get("DISCORD_TOKEN")
        self.owner = int(os.environ.get("DISCORD_OWNER_ID", "0"))
        
        self.bot_loop: asyncio.AbstractEventLoop | None = None

        self.tree = app_commands.CommandTree(self)

        @self.tree.command(name="ordis", description="Talk to Ordis")
        @app_commands.allowed_installs(guilds=False, users=True)
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        @app_commands.describe(message="What to say to Ordis", private="Only you can see the reply (on by default)")
        async def ordis(interaction: discord.Interaction, message: str, private: bool = True):  # pyright: ignore[reportUnusedFunction]
            await self.ask(interaction, message, private)

    async def setup_hook(self):
        self.bot_loop = asyncio.get_running_loop()
        await self.tree.sync()
        
    @property
    def online(self) -> bool:
        return self.bot_loop is not None and not self.is_closed()
    
    def notify(self, text: str):
        """DM the Operator from any thread, without a message to reply to."""

        if self.bot_loop is not None:
            asyncio.run_coroutine_threadsafe(self.send_dm(text), self.bot_loop)

    async def send_dm(self, text: str):
        try:
            user = await self.fetch_user(self.owner)
            for part in chunks(text):
                await user.send(part)
        except discord.HTTPException as err:
            print(f"[Discord] Couldn't DM the Operator: {err}")

    async def on_ready(self):
        print(f"[Discord] Online as {self.user}")

    async def on_message(self, message: discord.Message):
        """Plain messages in Ordis's own DM, no /ordis needed."""

        if message.author.id != self.owner or not isinstance(message.channel, discord.DMChannel):
            return
        if not message.content.strip():
            return

        await message.channel.typing()
        self.forward(message.content, self.replier(message.channel.send))

    async def ask(self, interaction: discord.Interaction, message: str, private: bool):
        if interaction.user.id != self.owner:
            await interaction.response.send_message("Ordis only answers to its Operator.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=private, thinking=True)
        self.forward(message, self.replier(lambda part: interaction.followup.send(part, ephemeral=private)))

    def replier(self, send: Send) -> Reply:
        """A reply function Ordis's main thread can call, which sends on the bot's own thread."""

        loop = asyncio.get_running_loop()

        async def send_all(text: str):
            for part in chunks(text):
                await send(part)

        def reply(text: str):
            asyncio.run_coroutine_threadsafe(send_all(text), loop)

        return reply

    def serve(self):
        try:
            asyncio.run(self.start(self.token or ""))
        except Exception as err:
            print(f"[Discord] Stopped: {err}")

    def launch(self):
        if not self.token or not self.owner:
            print("[Discord] No DISCORD_TOKEN or DISCORD_OWNER_ID in .env, skipping.")
            return

        threading.Thread(target=self.serve, daemon=True).start()
 