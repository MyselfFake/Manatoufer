import asyncio

import discord
from discord import app_commands

from botcore.features.twitch_api import TwitchApiError, get_twitch_stream, get_twitch_user, has_twitch_credentials
from botcore.features.twitch_state import normalize_twitch_login, toggle_twitch_notification
from botcore.runtime import bot


@bot.tree.command(name="mpub_twitch", description="Notifie un role quand une chaine Twitch passe en live.")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    chaine="Nom ou URL de la chaine Twitch.",
    role="Role a notifier.",
)
async def mpub_twitch_slash(interaction: discord.Interaction, chaine: str, role: discord.Role):
    try:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee dans un salon de serveur.",
                ephemeral=True,
            )
            return

        if not has_twitch_credentials():
            await interaction.response.send_message(
                "Configuration Twitch manquante: definis TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET.",
                ephemeral=True,
            )
            return

        twitch_login = normalize_twitch_login(chaine)
        if not twitch_login:
            await interaction.response.send_message("Nom de chaine Twitch invalide.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        user = await asyncio.to_thread(get_twitch_user, twitch_login)
        if user is None:
            await interaction.followup.send(f"Chaine Twitch `{twitch_login}` introuvable.", ephemeral=True)
            return

        stream = await asyncio.to_thread(get_twitch_stream, twitch_login)
        stream_id = stream.get("id") if isinstance(stream, dict) else None
        is_live = isinstance(stream_id, str) and bool(stream_id)
        display_name = str(user.get("display_name") or twitch_login)
        broadcaster_id = user.get("id")

        toggled = toggle_twitch_notification(
            guild_id=interaction.guild.id,
            twitch_login=twitch_login,
            channel_id=interaction.channel.id,
            role_id=role.id,
            broadcaster_id=str(broadcaster_id) if broadcaster_id is not None else None,
            display_name=display_name,
            is_live=is_live,
            last_stream_id=stream_id if isinstance(stream_id, str) else None,
        )

        if toggled:
            live_note = " Elle est deja live, donc je notifierai au prochain demarrage." if is_live else ""
            await interaction.followup.send(
                f"Notification Twitch activée : `{display_name}` notifiera {role.mention} dans {interaction.channel.mention}.{live_note}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                f"Notification Twitch désactivée pour `{display_name}` avec {role.mention} dans {interaction.channel.mention}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    except TwitchApiError as error:
        print(f"Erreur Twitch /mpub_twitch : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Impossible de contacter Twitch pour le moment.", ephemeral=True)
        else:
            await interaction.response.send_message("Impossible de contacter Twitch pour le moment.", ephemeral=True)
    except Exception as error:
        print(f"Erreur slash /mpub_twitch : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant la configuration Twitch.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant la configuration Twitch.",
                ephemeral=True,
            )
