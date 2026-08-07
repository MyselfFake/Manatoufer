import discord


def can_member_target_role(member: discord.Member | None, role: discord.Role | None) -> bool:
    if member is None or role is None:
        return False

    if role.is_default():
        return False

    if getattr(member, "guild_permissions", None) is not None and member.guild_permissions.administrator:
        return True

    guild = getattr(member, "guild", None)
    if guild is not None and getattr(member, "id", None) is not None and member.id == guild.owner_id:
        return True

    member_roles = getattr(member, "roles", []) or []
    return any(user_role > role for user_role in member_roles if user_role is not None)
