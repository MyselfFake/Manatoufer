import time

import discord
from discord import app_commands

from botcore.config import (
    MAX_MOOMLE_DURATION_HOURS,
    MAX_MOOMLE_SESSIONS,
    MAX_MOOMLE_SLOTS,
    MOOMLE_SLOT_REACTION_EMOJIS,
)
from botcore.features.events import find_event_channel_for_role_name
from botcore.features.moomle_formatting_helpers import (
    build_moomle_poll_embed,
    build_moomle_suggest_embed,
    get_session_display_name,
    list_moomle_session_roles,
    parse_semicolon_values,
)
from botcore.features.moomle_logic import build_moomle_suggestion_context, build_moomle_suggestion_lines_from_context
from botcore.features.moomle_state import (
    get_poll_copy,
    get_poll_creator_id,
    moomle_lock,
    moomle_polls,
    normalize_poll_key,
    resolve_existing_poll_key,
    set_poll_use_event_sessions,
    save_moomle_polls_to_disk,
)
from botcore.runtime import bot

CLASSIC_WEEK_SLOTS = [
    "Lundi",
    "Mardi",
    "Mercredi",
    "Jeudi",
    "Vendredi",
    "Samedi",
    "Dimanche",
]

OFF_WORK_SLOTS = [
    "Lundi soir",
    "Mardi soir",
    "Mercredi soir",
    "Jeudi soir",
    "Vendredi soir",
    "Samedi aprem",
    "Samedi soir",
    "Dimanche aprem",
    "Dimanche soir",
]


class SlotTemplateChoiceView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.selected_slots: list[str] | None = None
        self.selected_label: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut choisir ce modele.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Semaine classique", style=discord.ButtonStyle.primary)
    async def classic_week_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.selected_slots = list(CLASSIC_WEEK_SLOTS)
        self.selected_label = "Semaine classique"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Dispo hors-travail", style=discord.ButtonStyle.secondary)
    async def off_work_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.selected_slots = list(OFF_WORK_SLOTS)
        self.selected_label = "Dispo hors-travail"
        self.stop()
        await interaction.response.defer()


class PollModeChoiceView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.selected_use_event_sessions: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut choisir ce mode.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Avec events", style=discord.ButtonStyle.success)
    async def with_events_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.selected_use_event_sessions = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Sans events", style=discord.ButtonStyle.secondary)
    async def without_events_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.selected_use_event_sessions = False
        self.stop()
        await interaction.response.defer()


def _build_poll_session_labels(guild: discord.Guild, poll: dict) -> list[str]:
    use_event_sessions = poll.get("use_event_sessions", True) is not False
    if not use_event_sessions:
        return []

    session_labels = []
    for role_id in poll.get("session_role_ids", []):
        role = guild.get_role(role_id)
        if role is not None:
            session_labels.append(f"`{get_session_display_name(role.name)}`")
    return session_labels


async def _refresh_poll_message_embed(guild: discord.Guild, poll: dict):
    channel_id = poll.get("channel_id")
    message_id = poll.get("message_id")
    if not isinstance(channel_id, int) or not isinstance(message_id, int):
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.HTTPException:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(message_id)
    except discord.HTTPException:
        return

    session_labels = _build_poll_session_labels(guild, poll)
    try:
        await message.edit(
            embed=build_moomle_poll_embed(
                poll_name=poll.get("name", "Moomle"),
                slots=poll.get("slots", []),
                session_labels=session_labels,
                votes=poll.get("votes", {}),
                end_at_ts=poll.get("end_at_ts"),
                duration_hours=poll.get("duration_hours"),
                color=discord.Color.blurple(),
                use_event_sessions=poll.get("use_event_sessions", True) is not False,
            )
        )
    except discord.HTTPException:
        pass


async def _send_ephemeral(interaction: discord.Interaction, content: str):
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)

@bot.tree.command(name="moomle_pool_create", description="Cree un sondage de disponibilites (sessions detectees automatiquement).")
@app_commands.rename(poll_name="periode", slots="date", duration_hours="duree_sondage")
@app_commands.describe(
    poll_name="Periode (exemple: campagne-avril)",
    slots="Optionnel. Date(s) separee(s) par ; (ex: 2026-04-20 20:00;2026-04-23 20:00)",
    duration_hours=f"Duree du sondage en heures (1-{MAX_MOOMLE_DURATION_HOURS})",
)
async def moomle_pool_create_slash(
    interaction: discord.Interaction,
    poll_name: str,
    duration_hours: int,
    slots: str | None = None,
):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        parsed_slots = parse_semicolon_values(slots or "")
        if len(parsed_slots) == 0:
            slot_template_view = SlotTemplateChoiceView(author_id=interaction.user.id)
            await interaction.response.send_message(
                "Aucun creneau saisi. Choisis un modele de dates:",
                view=slot_template_view,
                ephemeral=True,
            )
            slot_prompt = await interaction.original_response()

            timed_out = await slot_template_view.wait()
            if timed_out or slot_template_view.selected_slots is None:
                await slot_prompt.edit(
                    content="Creation annulee: aucun modele de dates selectionne.",
                    view=None,
                )
                return

            parsed_slots = slot_template_view.selected_slots
            selected_label = slot_template_view.selected_label or "Mode personnalise"
            await slot_prompt.edit(
                content=f"Mode de creneaux choisi: **{selected_label}**.",
                view=None,
            )

        poll_key = normalize_poll_key(poll_name)
        guild_key = str(interaction.guild.id)
        end_at_ts = int(time.time()) + (duration_hours * 3600)

        if not poll_key:
            await _send_ephemeral(interaction, "Le nom du sondage est vide.")
            return
        if duration_hours < 1 or duration_hours > MAX_MOOMLE_DURATION_HOURS:
            await _send_ephemeral(
                interaction,
                f"La duree_sondage doit etre comprise entre 1 et {MAX_MOOMLE_DURATION_HOURS} heures.",
            )
            return
        if len(parsed_slots) > MAX_MOOMLE_SLOTS:
            await _send_ephemeral(interaction, f"Trop de creneaux (max {MAX_MOOMLE_SLOTS}).")
            return
        if len(parsed_slots) > len(MOOMLE_SLOT_REACTION_EMOJIS):
            await _send_ephemeral(
                interaction,
                f"Trop de creneaux pour les reactions disponibles (max {len(MOOMLE_SLOT_REACTION_EMOJIS)}).",
            )
            return

        detected_session_roles = list_moomle_session_roles(interaction.guild)
        role_ids = [role.id for role in detected_session_roles[:MAX_MOOMLE_SESSIONS]]

        async with moomle_lock:
            guild_polls = moomle_polls.setdefault(guild_key, {})
            if poll_key in guild_polls:
                await _send_ephemeral(interaction, f"Un sondage `{poll_name}` existe deja.")
                return

            guild_polls[poll_key] = {
                "name": poll_name.strip(),
                "created_by": interaction.user.id,
                "channel_id": interaction.channel_id,
                "message_id": None,
                "session_role_ids": role_ids,
                "use_event_sessions": True,
                "slots": parsed_slots,
                "votes": {},
                "duration_hours": duration_hours,
                "end_at_ts": end_at_ts,
                "auto_suggested": False,
            }
            save_moomle_polls_to_disk(moomle_polls)

        session_labels = []
        for role_id in role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None:
                session_labels.append(f"`{get_session_display_name(role.name)}`")

        embed = build_moomle_poll_embed(
            poll_name=poll_name.strip(),
            slots=parsed_slots,
            session_labels=session_labels,
            votes={},
            end_at_ts=end_at_ts,
            duration_hours=duration_hours,
            color=discord.Color.blurple(),
            use_event_sessions=True,
        )

        if interaction.response.is_done():
            poll_message = await interaction.followup.send(embed=embed, wait=True)
        else:
            await interaction.response.send_message(embed=embed)
            poll_message = await interaction.original_response()

        for slot_index in range(len(parsed_slots)):
            await poll_message.add_reaction(MOOMLE_SLOT_REACTION_EMOJIS[slot_index])

        async with moomle_lock:
            guild_polls = moomle_polls.get(guild_key, {})
            stored_poll = guild_polls.get(poll_key)
            if stored_poll is not None:
                stored_poll["message_id"] = poll_message.id
                guild_polls[poll_key] = stored_poll
                save_moomle_polls_to_disk(moomle_polls)

        mode_view = PollModeChoiceView(author_id=interaction.user.id)
        mode_prompt = await interaction.followup.send(
            "Prendre en compte les events pour ce sondage ?\n"
            "Par defaut: **Avec events**.",
            view=mode_view,
            ephemeral=True,
            wait=True,
        )

        timed_out = await mode_view.wait()
        if timed_out or mode_view.selected_use_event_sessions is None:
            await mode_prompt.edit(
                content="Mode conserve: **Avec events** (aucun choix recu).",
                view=None,
            )
            return

        selected_mode = mode_view.selected_use_event_sessions
        updated_poll, _ = await set_poll_use_event_sessions(
            guild_id=interaction.guild.id,
            poll_name=poll_name,
            use_event_sessions=selected_mode,
        )
        if updated_poll is None:
            await mode_prompt.edit(
                content="Sondage introuvable pour appliquer le mode.",
                view=None,
            )
            return

        await _refresh_poll_message_embed(interaction.guild, updated_poll)

        selected_label = "Avec events" if selected_mode else "Sans events"
        await mode_prompt.edit(
            content=f"Mode applique: **{selected_label}**.",
            view=None,
        )

    except Exception as error:
        print(f"Erreur slash /moomle_pool_create : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant la creation du moomle.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant la creation du moomle.",
                ephemeral=True,
            )


@bot.tree.command(name="moomle_status", description="Affiche l'etat du sondage de disponibilites.")
@app_commands.rename(poll_name="periode")
@app_commands.describe(poll_name="Periode")
async def moomle_status_slash(interaction: discord.Interaction, poll_name: str):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        poll, _ = await get_poll_copy(interaction.guild.id, poll_name)
        if poll is None:
            await interaction.response.send_message(
                f"Sondage `{poll_name}` introuvable.",
                ephemeral=True,
            )
            return

        slots: list[str] = poll.get("slots", [])
        votes: dict[str, dict[str, bool]] = poll.get("votes", {})

        session_names = _build_poll_session_labels(interaction.guild, poll)

        embed = build_moomle_poll_embed(
            poll_name=poll.get("name", poll_name),
            slots=slots,
            session_labels=session_names,
            votes=votes,
            end_at_ts=poll.get("end_at_ts"),
            duration_hours=poll.get("duration_hours"),
            color=discord.Color.green(),
            use_event_sessions=poll.get("use_event_sessions", True) is not False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as error:
        print(f"Erreur slash /moomle_status : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant la lecture du moomle.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant la lecture du moomle.",
                ephemeral=True,
            )


@bot.tree.command(name="moomle_pool_delete", description="Supprime un sondage moomle.")
@app_commands.rename(poll_name="periode")
@app_commands.describe(poll_name="Periode")
async def moomle_pool_delete_slash(interaction: discord.Interaction, poll_name: str):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        poll_key = normalize_poll_key(poll_name)
        guild_key = str(interaction.guild.id)

        async with moomle_lock:
            guild_polls = moomle_polls.get(guild_key, {})
            resolved_key = resolve_existing_poll_key(guild_polls, poll_name) or poll_key
            removed_poll = guild_polls.pop(resolved_key, None)
            if removed_poll is None:
                await interaction.response.send_message(
                    f"Sondage `{poll_name}` introuvable.",
                    ephemeral=True,
                )
                return

            save_moomle_polls_to_disk(moomle_polls)

        deleted_message = False
        channel_id = removed_poll.get("channel_id")
        message_id = removed_poll.get("message_id")

        if isinstance(channel_id, int) and isinstance(message_id, int):
            channel = interaction.guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await interaction.guild.fetch_channel(channel_id)
                except discord.HTTPException:
                    channel = None

            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete(reason=f"Suppression moomle '{poll_name}' par {interaction.user}")
                    deleted_message = True
                except discord.HTTPException:
                    deleted_message = False

        if deleted_message:
            await interaction.response.send_message(
                f"Sondage `{poll_name}` supprime (message retire).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Sondage `{poll_name}` supprime.",
                ephemeral=True,
            )

    except Exception as error:
        print(f"Erreur slash /moomle_pool_delete : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant la suppression du moomle.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant la suppression du moomle.",
                ephemeral=True,
            )


@bot.tree.command(
    name="moomle_pool_suggest",
    description="Propose automatiquement les sessions qui matchent les disponibilites.",
)
@app_commands.rename(poll_name="periode")
@app_commands.describe(poll_name="Periode")
async def moomle_pool_suggest_slash(interaction: discord.Interaction, poll_name: str):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        poll, _ = await get_poll_copy(interaction.guild.id, poll_name)
        if poll is None:
            await interaction.response.send_message(
                f"Sondage `{poll_name}` introuvable.",
                ephemeral=True,
            )
            return

        creator_id = get_poll_creator_id(poll)
        if creator_id is not None and interaction.user.id != creator_id:
            await interaction.response.send_message(
                "Seul le createur du sondage peut lancer /moomle_pool_suggest.",
                ephemeral=True,
            )
            return

        context, error_message = await build_moomle_suggestion_context(interaction.guild, poll)
        if error_message is not None:
            await interaction.response.send_message(
                error_message,
                ephemeral=True,
            )
            return

        if context is None:
            await interaction.response.send_message(
                "Impossible de calculer les suggestions pour ce sondage.",
                ephemeral=True,
            )
            return

        use_event_sessions = poll.get("use_event_sessions", True) is not False
        suggestion_lines = build_moomle_suggestion_lines_from_context(context)
        await interaction.response.send_message(
            embed=build_moomle_suggest_embed(
                poll,
                suggestion_lines or [],
                is_automatic=False,
                use_event_sessions=use_event_sessions,
            )
        )
        suggestion_message = await interaction.original_response()

        if not use_event_sessions:
            return

        # Notification des salons de session avec les dates ou la session est disponible.
        session_dates: dict[int, list[str]] = {}
        sessions_by_role_id: dict[int, dict] = {session["role_id"]: session for session in context["sessions"]}
        for slot_summary in context["slot_summaries"]:
            slot_label = slot_summary["slot_label"]
            for session in slot_summary["selected_sessions"]:
                role_id = session["role_id"]
                session_dates.setdefault(role_id, []).append(slot_label)

        for role_id, dates in session_dates.items():
            session = sessions_by_role_id.get(role_id)
            if session is None:
                continue
            event_channel = find_event_channel_for_role_name(interaction.guild, session["role_name"])
            if event_channel is None:
                continue
            dates_text = ", ".join(f"*{date_label}*" for date_label in dates[:12])
            if len(dates) > 12:
                dates_text += f", et {len(dates) - 12} autre(s)"
            try:
                await event_channel.send(
                    f"@here Relativement au sondage, pour la periode de *{poll.get('name', poll_name)}* "
                    f"vous avez des disponibilites pour {dates_text}.",
                    allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False),
                )
            except discord.HTTPException:
                pass

        # Fil de discussion pour les combinaisons de joueurs disponibles sans session existante.
        try:
            thread = await suggestion_message.create_thread(
                name=f"Combinaisons sans session - {poll.get('name', poll_name)}"
            )
        except discord.HTTPException:
            thread = None

        if thread is not None:
            for slot_summary in context["slot_summaries"]:
                available_user_ids = set(slot_summary["available_user_ids"])
                if len(available_user_ids) < 2:
                    continue

                has_exact_session = any(
                    session["required_user_ids"] == available_user_ids for session in context["sessions"]
                )
                if has_exact_session:
                    continue

                mentions = " ".join(f"<@{user_id}>" for user_id in sorted(available_user_ids))
                try:
                    await thread.send(
                        f"{mentions}, vous êtes disponibles simultanement *{slot_summary['slot_label']}* "
                        "mais n'avez pas encore de session rassemblant cette combinaison de personnes. "
                        "Si vous souhaitez jouer ensemble à cette date, n'hésitez pas à utiliser la commande "
                        "`/moomle_pool_create`.",
                        allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
                    )
                except discord.HTTPException:
                    pass

    except Exception as error:
        print(f"Erreur slash /moomle_pool_suggest : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant le calcul du moomle.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant le calcul du moomle.",
                ephemeral=True,
            )
