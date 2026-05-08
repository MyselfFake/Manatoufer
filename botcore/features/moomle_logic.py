import discord

from botcore.config import MOOMLE_SLOT_REACTION_EMOJIS
from botcore.features.moomle_formatting_helpers import get_session_display_name, list_moomle_session_roles


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


async def build_moomle_suggestion_context(guild: discord.Guild, poll: dict) -> tuple[dict | None, str | None]:
    slots: list[str] = poll.get("slots", [])
    votes: dict[str, dict[str, bool]] = poll.get("votes", {})
    respondents: set[int] = {int(user_id) for user_id in votes.keys() if str(user_id).isdigit()}

    if len(respondents) == 0:
        return None, "Aucun vote enregistre pour l'instant."

    use_event_sessions = poll.get("use_event_sessions", True) is not False

    slot_summaries = []
    for slot_index, slot_label in enumerate(slots, start=1):
        slot_key = str(slot_index)
        available_user_ids = {
            int(user_id)
            for user_id, user_votes in votes.items()
            if str(user_id).isdigit() and user_votes.get(slot_key) is True
        }

        slot_summaries.append(
            {
                "slot_index": slot_index,
                "slot_label": slot_label,
                "available_user_ids": available_user_ids,
            }
        )

    if not use_event_sessions:
        for slot_summary in slot_summaries:
            available_user_ids = slot_summary["available_user_ids"]
            selected_sessions = []
            if available_user_ids:
                selected_sessions = [
                    {
                        "role_id": None,
                        "role_name": "Participants disponibles",
                        "required_user_ids": available_user_ids,
                    }
                ]

            slot_summary["feasible_sessions"] = selected_sessions
            slot_summary["selected_sessions"] = selected_sessions

        return {
            "slots": slots,
            "sessions": [],
            "slot_summaries": slot_summaries,
            "use_event_sessions": False,
        }, None

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

    for slot_summary in slot_summaries:
        available_user_ids = slot_summary["available_user_ids"]
        feasible_sessions = [
            session
            for session in sessions
            if session["required_user_ids"] and session["required_user_ids"].issubset(available_user_ids)
        ]
        selected_sessions = pick_maximal_sessions(feasible_sessions) if feasible_sessions else []
        selected_sessions.sort(key=lambda session: (-len(session["required_user_ids"]), session["role_name"].lower()))

        slot_summary["feasible_sessions"] = feasible_sessions
        slot_summary["selected_sessions"] = selected_sessions

    return {
        "slots": slots,
        "sessions": sessions,
        "slot_summaries": slot_summaries,
        "use_event_sessions": True,
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
