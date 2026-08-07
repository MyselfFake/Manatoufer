import discord
from discord import app_commands

from botcore.features.mrole_state import register_mrole_message
from botcore.permissions import can_member_target_role
from botcore.runtime import bot


MAX_ROLE_REACTION_PAIRS = 20


def can_bot_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False
    return bot_member.guild_permissions.manage_roles and role < bot_member.top_role


def parse_emoji_role_pairs(raw_pairs: str) -> list[tuple[str, str]]:
    pairs = []
    for chunk in raw_pairs.split(";"):
        value = chunk.strip()
        if not value:
            continue
        if "=" not in value:
            raise ValueError("Chaque association doit utiliser le format emoji=role.")
        emoji, role_name = value.split("=", 1)
        emoji = emoji.strip()
        role_name = role_name.strip()
        if not emoji or not role_name:
            raise ValueError("Chaque association doit contenir un emoji et un role.")
        pairs.append((emoji, role_name))
    return pairs


async def get_or_create_role(guild: discord.Guild, role_name: str) -> discord.Role:
    role = discord.utils.get(guild.roles, name=role_name)
    if role is not None:
        return role
    return await guild.create_role(name=role_name, reason="Creation via /mrole_react")


@bot.tree.command(name="mrole_react", description="Cree un message qui attribue des roles via reactions.")
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(
    message="Texte du message a publier.",
    associations="Format: emoji=role;emoji=role (ex: 🎮=Joueur;📢=News)",
)
async def mrole_react_slash(interaction: discord.Interaction, message: str, associations: str):
    try:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee dans un salon de serveur.",
                ephemeral=True,
            )
            return
        if len(message) > 2000:
            await interaction.response.send_message(
                "Le message est trop long pour Discord (max 2000 caracteres).",
                ephemeral=True,
            )
            return

        if interaction.user is not None and not isinstance(interaction.user, discord.User):
            pass

        try:
            pairs = parse_emoji_role_pairs(associations)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        if len(pairs) == 0:
            await interaction.response.send_message("Ajoute au moins une association emoji=role.", ephemeral=True)
            return
        if len(pairs) > MAX_ROLE_REACTION_PAIRS:
            await interaction.response.send_message(
                f"Trop d'associations (max {MAX_ROLE_REACTION_PAIRS}).",
                ephemeral=True,
            )
            return

        seen_emojis = set()
        for emoji, _ in pairs:
            if emoji in seen_emojis:
                await interaction.response.send_message(
                    f"L'emoji `{emoji}` est utilise plusieurs fois.",
                    ephemeral=True,
                )
                return
            seen_emojis.add(emoji)

        await interaction.response.defer(ephemeral=True)

        emoji_roles: dict[str, int] = {}
        created_roles = []
        reused_roles = []
        unmanaged_roles = []
        for emoji, role_name in pairs:
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role is not None and not can_member_target_role(interaction.user, role):
                await interaction.response.send_message(
                    f"Tu ne peux pas gérer le rôle `{role.name}` : il n'est pas strictement en dessous de l'un de tes rôles hiérarchiques.",
                    ephemeral=True,
                )
                return
            if role is None:
                role = await interaction.guild.create_role(
                    name=role_name,
                    reason=f"Creation via /mrole_react par {interaction.user}",
                )
                created_roles.append(role.name)
            else:
                if not can_bot_manage_role(interaction.guild, role):
                    unmanaged_roles.append(role.name)
                    continue
                reused_roles.append(role.name)
            emoji_roles[emoji] = role.id

        if unmanaged_roles:
            for role in created_roles:
                try:
                    await role.delete(reason="Nettoyage apres roles mrole ingerables")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "Je ne peux pas attribuer ces roles existants: "
                + ", ".join(f"`{role_name}`" for role_name in unmanaged_roles)
                + ". Place mon role Discord au-dessus d'eux dans la hierarchie des roles, puis relance la commande.",
                ephemeral=True,
            )
            return

        published_message = await interaction.channel.send(message)
        added_reactions = []
        for emoji in emoji_roles.keys():
            try:
                await published_message.add_reaction(emoji)
                added_reactions.append(emoji)
            except discord.HTTPException:
                for role in created_roles:
                    try:
                        await role.delete(reason="Nettoyage apres reaction mrole invalide")
                    except discord.HTTPException:
                        pass
                try:
                    await published_message.delete()
                except discord.HTTPException:
                    pass
                await interaction.followup.send(
                    f"Impossible d'ajouter la reaction `{emoji}`. Verifie que l'emoji est valide pour ce serveur.",
                    ephemeral=True,
                )
                return

        register_mrole_message(
            guild_id=interaction.guild.id,
            message_id=published_message.id,
            channel_id=published_message.channel.id,
            emoji_roles=emoji_roles,
        )

        details = [f"Message cree avec {len(added_reactions)} reaction(s)."]
        if created_roles:
            details.append("Roles crees: " + ", ".join(f"`{role_name}`" for role_name in created_roles))
        if reused_roles:
            details.append("Roles existants: " + ", ".join(f"`{role_name}`" for role_name in reused_roles))
        await interaction.followup.send("\n".join(details), ephemeral=True)

    except discord.Forbidden:
        if interaction.response.is_done():
            await interaction.followup.send(
                "Je n'ai pas les permissions necessaires pour gerer ces roles/reactions.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Je n'ai pas les permissions necessaires pour gerer ces roles/reactions.",
                ephemeral=True,
            )
    except Exception as error:
        print(f"Erreur slash /mrole_react : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant la creation du mrole.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant la creation du mrole.",
                ephemeral=True,
            )
