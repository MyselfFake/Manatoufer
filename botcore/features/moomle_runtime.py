import asyncio
import json
import time

import discord

from botcore.config import MOOMLE_AUTO_SUGGEST_CHECK_SECONDS
from botcore.features.moomle_formatting_helpers import (
    build_moomle_poll_embed,
    build_moomle_suggest_embed,
    build_slot_emoji_to_index,
    get_session_display_name,
)
from botcore.features.moomle_logic import build_moomle_suggestion_context, build_moomle_suggestion_lines_from_context
from botcore.features.moomle_state import (
    find_poll_by_message_id,
    get_poll_end_timestamp,
    moomle_lock,
    moomle_polls,
    save_moomle_polls_to_disk,
)
from botcore.runtime import bot

async def handle_moomle_reaction_vote(payload: discord.RawReactionActionEvent, is_add: bool) -> bool:
    guild_id = payload.guild_id
    if guild_id is None:
        return False

    guild = bot.get_guild(guild_id)
    if guild is None:
        return False

    member = payload.member
    if member is None:
        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                member = None

    if member is not None and member.bot:
        return False

    guild_key = str(guild_id)
    emoji_text = str(payload.emoji)

    updated_poll_snapshot = None
    async with moomle_lock:
        guild_polls = moomle_polls.get(guild_key, {})
        poll_key, poll = find_poll_by_message_id(guild_polls, payload.message_id)
        if poll is None:
            return False

        slots = poll.get("slots", [])
        emoji_to_slot = build_slot_emoji_to_index(slots)
        slot_key = emoji_to_slot.get(emoji_text)
        if slot_key is None:
            return True

        votes = poll.setdefault("votes", {})
        user_key = str(payload.user_id)
        user_votes = votes.setdefault(user_key, {})

        if is_add:
            user_votes[slot_key] = True
        else:
            user_votes.pop(slot_key, None)
            if not user_votes:
                votes.pop(user_key, None)

        if poll_key is not None:
            guild_polls[poll_key] = poll
        updated_poll_snapshot = json.loads(json.dumps(poll))
        save_moomle_polls_to_disk(moomle_polls)

    if updated_poll_snapshot is None:
        return True

    channel_id = updated_poll_snapshot.get("channel_id")
    message_id = updated_poll_snapshot.get("message_id")
    if not isinstance(channel_id, int) or not isinstance(message_id, int):
        return True

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.HTTPException:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return True

    try:
        poll_message = await channel.fetch_message(message_id)
    except discord.HTTPException:
        return True

    use_event_sessions = updated_poll_snapshot.get("use_event_sessions", True) is not False
    session_labels = []
    if use_event_sessions:
        for role_id in updated_poll_snapshot.get("session_role_ids", []):
            role = guild.get_role(role_id)
            if role is not None:
                session_labels.append(f"`{get_session_display_name(role.name)}`")

    try:
        await poll_message.edit(
            embed=build_moomle_poll_embed(
                poll_name=updated_poll_snapshot.get("name", "Moomle"),
                slots=updated_poll_snapshot.get("slots", []),
                session_labels=session_labels,
                votes=updated_poll_snapshot.get("votes", {}),
                end_at_ts=updated_poll_snapshot.get("end_at_ts"),
                duration_hours=updated_poll_snapshot.get("duration_hours"),
                color=discord.Color.blurple(),
                use_event_sessions=use_event_sessions,
            )
        )
    except discord.HTTPException:
        pass

    return True

async def mark_poll_auto_suggested(guild_id: int, poll_key: str):
    guild_key = str(guild_id)
    async with moomle_lock:
        guild_polls = moomle_polls.get(guild_key, {})
        poll = guild_polls.get(poll_key)
        if poll is None:
            return
        poll["auto_suggested"] = True
        poll["auto_suggested_at_ts"] = int(time.time())
        guild_polls[poll_key] = poll
        save_moomle_polls_to_disk(moomle_polls)


async def run_due_moomle_auto_suggest():
    now_ts = int(time.time())
    due_polls: list[tuple[int, str, dict]] = []

    async with moomle_lock:
        for guild_key, guild_polls in moomle_polls.items():
            if not guild_key.isdigit():
                continue
            guild_id = int(guild_key)

            for poll_key, poll in guild_polls.items():
                end_ts = get_poll_end_timestamp(poll)
                if end_ts is None:
                    continue
                if poll.get("auto_suggested") is True:
                    continue
                if end_ts > now_ts:
                    continue
                due_polls.append((guild_id, poll_key, json.loads(json.dumps(poll))))

    for guild_id, poll_key, poll in due_polls:
        guild = bot.get_guild(guild_id)
        if guild is None:
            await mark_poll_auto_suggested(guild_id, poll_key)
            continue

        channel_id = poll.get("channel_id")
        if not isinstance(channel_id, int):
            await mark_poll_auto_suggested(guild_id, poll_key)
            continue

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            await mark_poll_auto_suggested(guild_id, poll_key)
            continue

        context, error_message = await build_moomle_suggestion_context(guild, poll)
        try:
            if error_message is not None:
                await channel.send(f"Fin du sondage moomle `{poll.get('name', poll_key)}`: {error_message}")
            elif context is not None:
                suggestion_lines = build_moomle_suggestion_lines_from_context(context)
                await channel.send(
                    embed=build_moomle_suggest_embed(
                        poll,
                        suggestion_lines,
                        is_automatic=True,
                        use_event_sessions=poll.get("use_event_sessions", True) is not False,
                    )
                )
        except discord.HTTPException:
            pass

        await mark_poll_auto_suggested(guild_id, poll_key)


async def moomle_auto_suggest_loop():
    while True:
        try:
            await run_due_moomle_auto_suggest()
        except Exception as error:
            print(f"Erreur auto-suggest moomle: {error}")
        await asyncio.sleep(MOOMLE_AUTO_SUGGEST_CHECK_SECONDS)
