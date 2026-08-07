import asyncio
import os
import threading

import discord
from discord import app_commands

from botcore import runtime
from botcore.config import GUILD_ID, TOKEN
from botcore.features import events as _events  # noqa: F401 - import side effects (slash commands)
from botcore.features import mrole as _mrole  # noqa: F401 - import side effects (slash commands)
from botcore.features import moomle as _moomle  # noqa: F401 - import side effects (slash commands)
from botcore.features import twitch as _twitch  # noqa: F401 - import side effects (slash commands)
from botcore.features.events import handle_event_reaction_add, handle_event_reaction_remove
from botcore.features.mrole import handle_mrole_reaction
from botcore.features.moomle import handle_moomle_reaction_vote, moomle_auto_suggest_loop
from botcore.features.twitch import twitch_live_notify_loop
from botcore.health import run_health_server
from botcore.runtime import bot


@bot.event
async def on_ready():
    print(f"Bot connecte en tant que {bot.user} !")

    if runtime.moomle_auto_suggest_task is None or runtime.moomle_auto_suggest_task.done():
        runtime.moomle_auto_suggest_task = asyncio.create_task(moomle_auto_suggest_loop())
        print("Moomle auto-suggest scheduler demarre.")

    if runtime.twitch_live_notify_task is None or runtime.twitch_live_notify_task.done():
        runtime.twitch_live_notify_task = asyncio.create_task(twitch_live_notify_loop())
        print("Twitch live scheduler demarre.")

    if runtime.commands_synced:
        return

    try:
        if GUILD_ID is not None:
            synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print(f"Slash commands sync (guild {GUILD_ID}): {len(synced)}")
        else:
            for guild in bot.guilds:
                try:
                    # Nettoie les anciennes commandes "guild-scoped" pour eviter les doublons
                    # quand des commandes globales existent aussi.
                    bot.tree.clear_commands(guild=guild)
                    await bot.tree.sync(guild=guild)
                except Exception as guild_error:
                    print(f"Echec nettoyage slash commands guild {guild.id}: {guild_error}")
            synced = await bot.tree.sync()
            print(f"Slash commands sync globaux: {len(synced)}")
        runtime.commands_synced = True
    except Exception as error:
        print(f"Erreur sync slash commands: {error}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        if bot.user and payload.user_id == bot.user.id:
            return

        moomle_handled = await handle_moomle_reaction_vote(payload, is_add=True)
        if moomle_handled:
            return

        mrole_handled = await handle_mrole_reaction(payload, is_add=True)
        if mrole_handled:
            return

        await handle_event_reaction_add(payload)

    except Exception as error:
        print(f"Erreur (ajout de role): {error}")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    try:
        if bot.user and payload.user_id == bot.user.id:
            return

        moomle_handled = await handle_moomle_reaction_vote(payload, is_add=False)
        if moomle_handled:
            return

        mrole_handled = await handle_mrole_reaction(payload, is_add=False)
        if mrole_handled:
            return

        await handle_event_reaction_remove(payload)

    except Exception as error:
        print(f"Erreur (retrait de role): {error}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"Erreur slash command: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue avec la commande slash.", ephemeral=True)
        else:
            await interaction.response.send_message("Une erreur est survenue avec la commande slash.", ephemeral=True)
    except (discord.InteractionResponded, discord.HTTPException):
        try:
            await interaction.followup.send("Une erreur est survenue avec la commande slash.", ephemeral=True)
        except discord.HTTPException:
            pass


def run_bot():
    if os.environ.get("PORT"):
        threading.Thread(target=run_health_server, daemon=True).start()

    if not TOKEN:
        raise RuntimeError(
            "Token Discord manquant. Definis la variable d'environnement DISCORD_TOKEN (ou TOKEN) avant de lancer le bot."
        )

    bot.run(TOKEN)
