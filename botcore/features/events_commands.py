import discord
from discord import app_commands

from botcore.config import CHECK_EMOJI, DELETE_CANCEL_EMOJI, DELETE_CONFIRM_EMOJI
from botcore.features.events_core import (
    delete_event_resources,
    ensure_event_setup,
    normalize_event_key,
    register_event_message,
    rename_event_resources,
    resolve_event_entities,
)
from botcore.permissions import can_member_target_role
from botcore.runtime import bot
from botcore.views import DeleteConfirmView

@bot.tree.command(name="moomle_event_create", description="Cree un event (role + salon prive).")
@app_commands.describe(event_name="Nom de l'event (exemple: Test)")
async def create_event_slash(interaction: discord.Interaction, event_name: str):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        # Ack immediat: en multi-instance, une seule instance peut confirmer l'interaction.
        # Les autres sortent sans lancer les creations.
        try:
            await interaction.response.defer(ephemeral=True)
        except (discord.InteractionResponded, discord.HTTPException):
            return

        event_channel, _, role_name = await ensure_event_setup(interaction.guild, event_name)

        embed = discord.Embed(
            title=f"Evenement : {event_name}",
            description=f"Reagissez avec {CHECK_EMOJI} pour obtenir le role !",
            color=discord.Color.random(),
        )
        if interaction.channel is None:
            await interaction.followup.send(
                "Impossible de publier l'event dans ce contexte.",
                ephemeral=True,
            )
            return

        message = await interaction.channel.send(embed=embed)
        await message.add_reaction(CHECK_EMOJI)

        await register_event_message(message, role_name)
        await interaction.followup.send(
            f"Event cree: role `{role_name}` et salon {event_channel.mention}.",
            ephemeral=True,
        )

    except Exception as e:
        print(f"Erreur slash /moomle_event_create : {e}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Une erreur est survenue pendant la creation de l'event.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Une erreur est survenue pendant la creation de l'event.",
                    ephemeral=True,
                )
        except (discord.InteractionResponded, discord.HTTPException):
            try:
                await interaction.followup.send(
                    "Une erreur est survenue pendant la creation de l'event.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass


@bot.tree.command(name="moomle_event_change", description="Renomme un event (role + salon associes).")
@app_commands.describe(
    old_event_name="Nom actuel de l'event",
    new_event_name="Nouveau nom de l'event",
)
async def change_event_slash(
    interaction: discord.Interaction,
    old_event_name: str,
    new_event_name: str,
):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        old_name = old_event_name.strip()
        new_name = new_event_name.strip()
        if not old_name or not new_name:
            await interaction.response.send_message(
                "Les deux noms d'event sont obligatoires.",
                ephemeral=True,
            )
            return

        if normalize_event_key(old_name) == normalize_event_key(new_name):
            await interaction.response.send_message(
                "Le nouveau nom est identique au nom actuel.",
                ephemeral=True,
            )
            return

        event_channel, role, _ = resolve_event_entities(interaction.guild, old_name)
        if role is not None and not can_member_target_role(interaction.user, role):
            await interaction.response.send_message(
                "Tu ne peux pas renommer cet événement : son rôle n'est pas strictement en dessous de l'un de tes rôles hiérarchiques.",
                ephemeral=True,
            )
            return

        success, result_message = await rename_event_resources(
            interaction.guild,
            old_name,
            new_name,
            str(interaction.user),
        )

        await interaction.response.send_message(result_message, ephemeral=True)

    except Exception as e:
        print(f"Erreur slash /moomle_event_change : {e}")
        if interaction.response.is_done():
            await interaction.followup.send(
                "Une erreur est survenue pendant le renommage de l'event.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant le renommage de l'event.",
                ephemeral=True,
            )


@bot.tree.command(name="moomle_event_delete", description="Supprime un event (role + salon) avec confirmation.")
@app_commands.describe(event_name="Nom de l'event a supprimer")
async def delete_event_slash(interaction: discord.Interaction, event_name: str):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        event_channel, role, _ = resolve_event_entities(interaction.guild, event_name)
        if event_channel is None and role is None:
            await interaction.response.send_message(
                f"Aucun event trouve pour `{event_name}`.",
                ephemeral=True,
            )
            return

        if role is not None and not can_member_target_role(interaction.user, role):
            await interaction.response.send_message(
                "Tu ne peux pas supprimer cet événement : son rôle n'est pas strictement en dessous de l'un de tes rôles hiérarchiques.",
                ephemeral=True,
            )
            return

        to_delete = []
        if event_channel is not None:
            to_delete.append(f"salon `{event_channel.name}`")
        if role is not None:
            to_delete.append(f"role `{role.name}`")

        view = DeleteConfirmView(
            author_id=interaction.user.id,
            confirm_emoji=DELETE_CONFIRM_EMOJI,
            cancel_emoji=DELETE_CANCEL_EMOJI,
        )
        await interaction.response.send_message(
            "Confirmation requise: clique sur Confirmer pour supprimer "
            f"{', '.join(to_delete)}.",
            view=view,
            ephemeral=True,
        )

        timed_out = await view.wait()
        if timed_out:
            await interaction.edit_original_response(content="Suppression annulee (delai depasse).", view=None)
            return

        if not view.confirmed:
            await interaction.edit_original_response(content="Suppression annulee.", view=None)
            return

        deleted_labels = await delete_event_resources(interaction.guild, event_name, str(interaction.user))
        if deleted_labels is None:
            await interaction.edit_original_response(content=f"Aucun event trouve pour `{event_name}`.", view=None)
            return

        await interaction.edit_original_response(
            content=f"Suppression terminee: {', '.join(deleted_labels)}.",
            view=None,
        )

    except Exception as e:
        print(f"Erreur slash /moomle_event_delete : {e}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Une erreur est survenue pendant la suppression de l'event.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Une erreur est survenue pendant la suppression de l'event.",
                    ephemeral=True,
                )
        except (discord.InteractionResponded, discord.HTTPException):
            try:
                await interaction.followup.send(
                    "Une erreur est survenue pendant la suppression de l'event.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass
