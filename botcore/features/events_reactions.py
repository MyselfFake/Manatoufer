import discord

from botcore.config import CHECK_EMOJI
from botcore.features.events_core import find_event_channel_for_role_name
from botcore.features.events_state import active_events
from botcore.runtime import bot

async def handle_event_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != CHECK_EMOJI:
        return

    role_name = active_events.get(payload.message_id)
    if role_name is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    member = payload.member
    if member is None:
        member = guild.get_member(payload.user_id)
        if member is None:
            member = await guild.fetch_member(payload.user_id)

    if member.bot:
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color.random(),
        )
        print(f"Role '{role_name}' cree !")

    await member.add_roles(role)
    print(f"Role '{role_name}' attribue a {member.display_name} !")

    event_channel = find_event_channel_for_role_name(guild, role_name)
    if event_channel is not None:
        await event_channel.send(
            f"{member.mention} a rejoint l'event.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )


async def handle_event_reaction_remove(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != CHECK_EMOJI:
        return

    role_name = active_events.get(payload.message_id)
    if role_name is None:
        print("Aucun evenement actif pour cet emoji.")
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None:
        member = await guild.fetch_member(payload.user_id)

    if member.bot:
        return

    await member.remove_roles(role)
    print(f"Role '{role_name}' retire a {member.display_name} !")
