import discord

from botcore.config import MM_EVENT_PREFIX, MOOMLE_SLOT_REACTION_EMOJIS
from botcore.features.events import (
    event_resources,
    extract_event_name_from_role_name,
    find_event_category,
    find_event_role,
)
from botcore.moomle_formatting import (
    build_moomle_poll_embed as core_build_moomle_poll_embed,
    build_moomle_suggest_embed as core_build_moomle_suggest_embed,
    build_slot_emoji_to_index as core_build_slot_emoji_to_index,
    parse_semicolon_values as core_parse_semicolon_values,
    render_slot_lines_with_emojis as core_render_slot_lines_with_emojis,
)

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
    use_event_sessions: bool = True,
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
        use_event_sessions=use_event_sessions,
    )

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

def build_moomle_suggest_embed(
    poll: dict,
    suggestion_lines: list[str],
    is_automatic: bool,
    use_event_sessions: bool = True,
) -> discord.Embed:
    return core_build_moomle_suggest_embed(
        poll=poll,
        suggestion_lines=suggestion_lines,
        is_automatic=is_automatic,
        use_event_sessions=use_event_sessions,
    )
