import discord

from botcore.features.mrole_state import get_mrole_role_id
from botcore.runtime import bot


async def handle_mrole_reaction(payload: discord.RawReactionActionEvent, is_add: bool) -> bool:
    guild_id = payload.guild_id
    if guild_id is None:
        return False

    role_id = get_mrole_role_id(guild_id, payload.message_id, str(payload.emoji))
    if role_id is None:
        return False

    guild = bot.get_guild(guild_id)
    if guild is None:
        return True

    role = guild.get_role(role_id)
    if role is None:
        return True

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles or role >= bot_member.top_role:
        print(
            f"Erreur mrole reaction role {role.id}: role ingerable "
            "(permission Manage Roles manquante ou role trop haut)."
        )
        return True

    member = payload.member
    if member is None:
        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                member = None

    if member is None or member.bot:
        return True

    try:
        if is_add:
            await member.add_roles(role, reason="Role reaction mrole")
        else:
            await member.remove_roles(role, reason="Retrait reaction mrole")
    except discord.HTTPException as error:
        print(f"Erreur mrole reaction role {role.id}: {error}")

    return True
