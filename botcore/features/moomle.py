import asyncio
import json
import os
import time

import discord
from discord import app_commands

from botcore.config import (
    MM_EVENT_PREFIX,
    MAX_MOOMLE_DURATION_HOURS,
    MAX_MOOMLE_SESSIONS,
    MAX_MOOMLE_SLOTS,
    MOOMLE_AUTO_SUGGEST_CHECK_SECONDS,
    MOOMLE_SLOT_REACTION_EMOJIS,
    MOOMLE_STORAGE_FILE,
)
from botcore.features.events import (
    event_resources,
    extract_event_name_from_role_name,
    find_event_category,
    find_event_channel_for_role_name,
    find_event_role,
    normalize_event_key,
)
from botcore.moomle_formatting import (
    build_moomle_poll_embed as core_build_moomle_poll_embed,
    build_moomle_suggest_embed as core_build_moomle_suggest_embed,
    build_slot_emoji_to_index as core_build_slot_emoji_to_index,
    parse_semicolon_values as core_parse_semicolon_values,
    render_slot_lines_with_emojis as core_render_slot_lines_with_emojis,
)
from botcore.runtime import bot
from botcore.storage import load_json_mapping, save_json_mapping

moomle_polls: dict[str, dict[str, dict]] = {}
moomle_lock: asyncio.Lock = asyncio.Lock()

def get_moomle_storage_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, MOOMLE_STORAGE_FILE)


def load_moomle_polls_from_disk() -> dict[str, dict[str, dict]]:
    path = get_moomle_storage_path()
    payload = load_json_mapping(path)
    if payload:
        return payload
    if os.path.exists(path):
        print(f"Erreur chargement moomle ({path}): donnees invalides.")
    return {}


def save_moomle_polls_to_disk(payload: dict[str, dict[str, dict]]):
    path = get_moomle_storage_path()
    if not save_json_mapping(path, payload):
        print(f"Erreur sauvegarde moomle ({path}).")


def normalize_poll_key(name: str) -> str:
    return normalize_event_key(name)


def build_slot_emoji_to_index(slots: list[str]) -> dict[str, str]:
    return core_build_slot_emoji_to_index(slots, MOOMLE_SLOT_REACTION_EMOJIS)


def render_slot_lines_with_emojis(slots: list[str]) -> list[str]:
    return core_render_slot_lines_with_emojis(slots, MOOMLE_SLOT_REACTION_EMOJIS)


def build_moomle_poll_embed(
    poll_name: str,
    slots: list[str],
    session_labels: list[str],
    votes: dict[str, dict[str, bool]],
    end_at_ts: int | None,
    duration_hours: int | None,
    color: discord.Color,
) -> discord.Embed:
    return core_build_moomle_poll_embed(
        poll_name=poll_name,
        slots=slots,
        session_labels=session_labels,
        votes=votes,
        end_at_ts=end_at_ts,
        duration_hours=duration_hours,
        color=color,
        reaction_emojis=MOOMLE_SLOT_REACTION_EMOJIS,
    )


def find_poll_by_message_id(guild_polls: dict[str, dict], message_id: int) -> tuple[str, dict] | tuple[None, None]:
    for poll_key, poll in guild_polls.items():
        if poll.get("message_id") == message_id:
            return poll_key, poll
    return None, None


def parse_semicolon_values(raw_value: str) -> list[str]:
    return core_parse_semicolon_values(raw_value)


def get_session_display_name(role_name: str) -> str:
    extracted_event_name = extract_event_name_from_role_name(role_name)
    if extracted_event_name:
        return extracted_event_name
    return role_name


def extract_event_name_from_channel_name(channel_name: str) -> str:
    if "|" in channel_name:
        return channel_name.split("|", 1)[1].strip()
    return channel_name.strip()


def list_moomle_session_roles(guild: discord.Guild) -> list[discord.Role]:
    roles_by_id: dict[int, discord.Role] = {}
    category = find_event_category(guild)

    for role in guild.roles:
        if role.is_default():
            continue
        if role.name.lower().startswith(MM_EVENT_PREFIX):
            roles_by_id[role.id] = role

    if category is not None:
        for channel in category.text_channels:
            event_name = extract_event_name_from_channel_name(channel.name)
            if event_name:
                role = find_event_role(guild, event_name)
                if role is not None:
                    roles_by_id[role.id] = role
                    continue

            for overwrite_target, overwrite in channel.overwrites.items():
                if not isinstance(overwrite_target, discord.Role):
                    continue
                if overwrite_target.is_default():
                    continue
                if overwrite.view_channel is False:
                    continue
                roles_by_id[overwrite_target.id] = overwrite_target

    for tracked in event_resources.values():
        role_name = tracked.get("role_name")
        if not isinstance(role_name, str):
            continue
        role = discord.utils.get(guild.roles, name=role_name)
        if role is not None:
            roles_by_id[role.id] = role

    roles = list(roles_by_id.values())
    roles.sort(key=lambda role: get_session_display_name(role.name).lower())
    return roles


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

    session_labels = []
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
            )
        )
    except discord.HTTPException:
        pass

    return True


moomle_polls = load_moomle_polls_from_disk()



def pick_maximal_sessions(feasible_sessions: list[dict]) -> list[dict]:
    maximal_sessions = []

    for session in feasible_sessions:
        required_users = session["required_user_ids"]
        has_strict_superset = any(
            required_users < other_session["required_user_ids"] for other_session in feasible_sessions
        )
        if not has_strict_superset:
            maximal_sessions.append(session)

    deduped = []
    seen_signatures: set[tuple[int, tuple[int, ...]]] = set()
    for session in maximal_sessions:
        signature = (session["role_id"], tuple(sorted(session["required_user_ids"])))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(session)

    return deduped


async def get_poll_copy(guild_id: int, poll_name: str) -> tuple[dict | None, str]:
    poll_key = normalize_poll_key(poll_name)
    guild_key = str(guild_id)

    async with moomle_lock:
        guild_polls = moomle_polls.get(guild_key, {})
        poll = guild_polls.get(poll_key)
        if poll is None:
            return None, poll_key
        return json.loads(json.dumps(poll)), poll_key


def get_poll_creator_id(poll: dict) -> int | None:
    created_by = poll.get("created_by")
    if isinstance(created_by, int):
        return created_by
    if isinstance(created_by, str) and created_by.isdigit():
        return int(created_by)
    return None


def get_poll_end_timestamp(poll: dict) -> int | None:
    end_at = poll.get("end_at_ts")
    if isinstance(end_at, int):
        return end_at
    if isinstance(end_at, float):
        return int(end_at)
    if isinstance(end_at, str) and end_at.isdigit():
        return int(end_at)
    return None


async def build_moomle_suggestion_context(guild: discord.Guild, poll: dict) -> tuple[dict | None, str | None]:
    slots: list[str] = poll.get("slots", [])
    votes: dict[str, dict[str, bool]] = poll.get("votes", {})
    respondents: set[int] = {int(user_id) for user_id in votes.keys() if str(user_id).isdigit()}

    if len(respondents) == 0:
        return None, "Aucun vote enregistre pour l'instant."

    candidate_roles_by_id: dict[int, discord.Role] = {}
    for role in list_moomle_session_roles(guild):
        candidate_roles_by_id[role.id] = role
    for role_id in poll.get("session_role_ids", []):
        role = guild.get_role(role_id)
        if role is not None:
            candidate_roles_by_id[role.id] = role

    candidate_role_ids = set(candidate_roles_by_id.keys())
    role_members_by_id: dict[int, set[int]] = {role_id: set() for role_id in candidate_role_ids}

    try:
        async for member in guild.fetch_members(limit=None):
            if member.bot:
                continue
            for role in member.roles:
                if role.id in candidate_role_ids:
                    role_members_by_id[role.id].add(member.id)
    except discord.HTTPException:
        try:
            await guild.chunk(cache=True)
        except discord.HTTPException:
            pass
        for role in candidate_roles_by_id.values():
            role_members_by_id[role.id] = {member.id for member in role.members if not member.bot}

    sessions = []
    for role in candidate_roles_by_id.values():
        role_member_ids = role_members_by_id.get(role.id, set())
        if len(role_member_ids) == 0:
            continue

        sessions.append(
            {
                "role_id": role.id,
                "role_name": role.name,
                "required_user_ids": role_member_ids,
            }
        )

    if len(sessions) == 0:
        return None, "Aucun role de session detecte."

    slot_summaries = []
    for slot_index, slot_label in enumerate(slots, start=1):
        slot_key = str(slot_index)
        available_user_ids = {
            int(user_id)
            for user_id, user_votes in votes.items()
            if str(user_id).isdigit() and user_votes.get(slot_key) is True
        }

        feasible_sessions = [
            session
            for session in sessions
            if session["required_user_ids"] and session["required_user_ids"].issubset(available_user_ids)
        ]
        selected_sessions = pick_maximal_sessions(feasible_sessions) if feasible_sessions else []
        selected_sessions.sort(key=lambda session: (-len(session["required_user_ids"]), session["role_name"].lower()))

        slot_summaries.append(
            {
                "slot_index": slot_index,
                "slot_label": slot_label,
                "available_user_ids": available_user_ids,
                "feasible_sessions": feasible_sessions,
                "selected_sessions": selected_sessions,
            }
        )

    return {
        "slots": slots,
        "sessions": sessions,
        "slot_summaries": slot_summaries,
    }, None


def build_moomle_suggestion_lines_from_context(context: dict) -> list[str]:
    suggestion_lines = []
    for slot_summary in context["slot_summaries"]:
        slot_index = slot_summary["slot_index"]
        slot_label = slot_summary["slot_label"]
        selected_sessions = slot_summary["selected_sessions"]
        slot_emoji = (
            MOOMLE_SLOT_REACTION_EMOJIS[slot_index - 1]
            if slot_index - 1 < len(MOOMLE_SLOT_REACTION_EMOJIS)
            else "•"
        )

        if len(selected_sessions) == 0:
            suggestion_lines.append(f"{slot_emoji} {slot_index}. {slot_label} -> aucune session")
            continue

        rendered_sessions = []
        for session in selected_sessions:
            player_mentions = ", ".join(f"<@{user_id}>" for user_id in sorted(session["required_user_ids"]))
            rendered_sessions.append(
                f"`{get_session_display_name(session['role_name'])}` ({len(session['required_user_ids'])} joueurs: {player_mentions})"
            )

        suggestion_lines.append(f"{slot_emoji} {slot_index}. {slot_label} -> " + " | ".join(rendered_sessions))

    return suggestion_lines


def build_moomle_suggest_embed(poll: dict, suggestion_lines: list[str], is_automatic: bool) -> discord.Embed:
    return core_build_moomle_suggest_embed(poll, suggestion_lines, is_automatic)


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
                await channel.send(embed=build_moomle_suggest_embed(poll, suggestion_lines, is_automatic=True))
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


@bot.tree.command(name="moomle_pool_create", description="Cree un sondage de disponibilites (sessions detectees automatiquement).")
@app_commands.rename(poll_name="periode", slots="date", duration_hours="duree_sondage")
@app_commands.describe(
    poll_name="Periode (exemple: campagne-avril)",
    slots="Date(s) separee(s) par ; (ex: 2026-04-20 20:00;2026-04-23 20:00)",
    duration_hours=f"Duree du sondage en heures (1-{MAX_MOOMLE_DURATION_HOURS})",
)
async def moomle_pool_create_slash(
    interaction: discord.Interaction,
    poll_name: str,
    slots: str,
    duration_hours: int,
):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        parsed_slots = parse_semicolon_values(slots)
        poll_key = normalize_poll_key(poll_name)
        guild_key = str(interaction.guild.id)
        end_at_ts = int(time.time()) + (duration_hours * 3600)

        if not poll_key:
            await interaction.response.send_message("Le nom du sondage est vide.", ephemeral=True)
            return
        if duration_hours < 1 or duration_hours > MAX_MOOMLE_DURATION_HOURS:
            await interaction.response.send_message(
                f"La duree_sondage doit etre comprise entre 1 et {MAX_MOOMLE_DURATION_HOURS} heures.",
                ephemeral=True,
            )
            return
        if len(parsed_slots) == 0:
            await interaction.response.send_message("Ajoute au moins un creneau.", ephemeral=True)
            return
        if len(parsed_slots) > MAX_MOOMLE_SLOTS:
            await interaction.response.send_message(
                f"Trop de creneaux (max {MAX_MOOMLE_SLOTS}).",
                ephemeral=True,
            )
            return
        if len(parsed_slots) > len(MOOMLE_SLOT_REACTION_EMOJIS):
            await interaction.response.send_message(
                f"Trop de creneaux pour les reactions disponibles (max {len(MOOMLE_SLOT_REACTION_EMOJIS)}).",
                ephemeral=True,
            )
            return

        detected_session_roles = list_moomle_session_roles(interaction.guild)
        role_ids = [role.id for role in detected_session_roles[:MAX_MOOMLE_SESSIONS]]

        async with moomle_lock:
            guild_polls = moomle_polls.setdefault(guild_key, {})
            if poll_key in guild_polls:
                await interaction.response.send_message(
                    f"Un sondage `{poll_name}` existe deja.",
                    ephemeral=True,
                )
                return

            guild_polls[poll_key] = {
                "name": poll_name.strip(),
                "created_by": interaction.user.id,
                "channel_id": interaction.channel_id,
                "message_id": None,
                "session_role_ids": role_ids,
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
        )

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

        session_names = []
        for role_id in poll.get("session_role_ids", []):
            role = interaction.guild.get_role(role_id)
            if role is not None:
                session_names.append(f"`{get_session_display_name(role.name)}`")

        embed = build_moomle_poll_embed(
            poll_name=poll.get("name", poll_name),
            slots=slots,
            session_labels=session_names,
            votes=votes,
            end_at_ts=poll.get("end_at_ts"),
            duration_hours=poll.get("duration_hours"),
            color=discord.Color.green(),
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
            removed_poll = guild_polls.pop(poll_key, None)
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

        suggestion_lines = build_moomle_suggestion_lines_from_context(context)
        await interaction.response.send_message(embed=build_moomle_suggest_embed(poll, suggestion_lines or [], is_automatic=False))
        suggestion_message = await interaction.original_response()

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



