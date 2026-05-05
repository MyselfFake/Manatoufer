import asyncio

from discord.ext import commands

from botcore.config import build_intents


intents = build_intents()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

commands_synced = False
moomle_auto_suggest_task: asyncio.Task | None = None
