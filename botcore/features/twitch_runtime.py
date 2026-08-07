import asyncio

import discord

from botcore.config import TWITCH_CHECK_SECONDS
from botcore.features.twitch_api import TwitchApiError, get_twitch_stream, has_twitch_credentials
from botcore.features.twitch_state import twitch_notifications, update_twitch_notification_state
from botcore.runtime import bot


def build_twitch_live_message(role: discord.Role, display_name: str, twitch_login: str, stream: dict) -> str:
    title = stream.get("title")
    game_name = stream.get("game_name")
    live_url = f"https://www.twitch.tv/{twitch_login}"

    lines = [f"{role.mention} `{display_name}` est en live sur Twitch: {live_url}"]
    if isinstance(title, str) and title:
        lines.append(f"**{title}**")
    if isinstance(game_name, str) and game_name:
        lines.append(f"Jeu: {game_name}")
    return "\n".join(lines)


async def run_twitch_live_check():
    if not has_twitch_credentials():
        return

    snapshot: list[tuple[int, str, dict]] = []
    for guild_key, guild_notifications in twitch_notifications.items():
        if not guild_key.isdigit() or not isinstance(guild_notifications, dict):
            continue
        for twitch_login, notification in guild_notifications.items():
            if isinstance(notification, dict):
                snapshot.append((int(guild_key), twitch_login, dict(notification)))

    for guild_id, twitch_login, notification in snapshot:
        try:
            stream = await asyncio.to_thread(get_twitch_stream, twitch_login)
        except TwitchApiError as error:
            print(f"Erreur check Twitch {twitch_login}: {error}")
            continue

        previous_live = notification.get("is_live") is True
        previous_stream_id = notification.get("last_stream_id")
        current_stream_id = stream.get("id") if isinstance(stream, dict) else None
        current_live = isinstance(current_stream_id, str) and bool(current_stream_id)

        should_notify = current_live and (not previous_live or previous_stream_id != current_stream_id)
        if not should_notify:
            update_twitch_notification_state(
                guild_id=guild_id,
                twitch_login=twitch_login,
                is_live=current_live,
                last_stream_id=current_stream_id if current_live else None,
            )
            continue

        guild = bot.get_guild(guild_id)
        if guild is None:
            update_twitch_notification_state(guild_id, twitch_login, True, current_stream_id)
            continue

        channel_id = notification.get("channel_id")
        role_id = notification.get("role_id")
        channel = guild.get_channel(channel_id) if isinstance(channel_id, int) else None
        role = guild.get_role(role_id) if isinstance(role_id, int) else None

        if channel is None and isinstance(channel_id, int):
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None

        if isinstance(channel, discord.TextChannel) and role is not None and stream is not None:
            display_name = str(notification.get("display_name") or twitch_login)
            try:
                await channel.send(
                    build_twitch_live_message(role, display_name, twitch_login, stream),
                    allowed_mentions=discord.AllowedMentions(roles=[role], users=False, everyone=False),
                )
            except discord.HTTPException as error:
                print(f"Erreur notification Twitch {twitch_login}: {error}")

        update_twitch_notification_state(guild_id, twitch_login, True, current_stream_id)


async def twitch_live_notify_loop():
    while True:
        try:
            await run_twitch_live_check()
        except Exception as error:
            print(f"Erreur scheduler Twitch: {error}")
        await asyncio.sleep(TWITCH_CHECK_SECONDS)
