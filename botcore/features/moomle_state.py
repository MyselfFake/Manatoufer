import asyncio
import json
import os

from botcore.config import MOOMLE_STORAGE_FILE
from botcore.features.events import normalize_event_key
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


def resolve_existing_poll_key(guild_polls: dict[str, dict], poll_name: str) -> str | None:
    normalized_target = normalize_poll_key(poll_name)
    if normalized_target in guild_polls:
        return normalized_target

    for stored_key in guild_polls.keys():
        if normalize_poll_key(stored_key) == normalized_target:
            return stored_key

    return None

def find_poll_by_message_id(guild_polls: dict[str, dict], message_id: int) -> tuple[str, dict] | tuple[None, None]:
    for poll_key, poll in guild_polls.items():
        if poll.get("message_id") == message_id:
            return poll_key, poll
    return None, None

async def get_poll_copy(guild_id: int, poll_name: str) -> tuple[dict | None, str]:
    poll_key = normalize_poll_key(poll_name)
    guild_key = str(guild_id)

    async with moomle_lock:
        guild_polls = moomle_polls.get(guild_key, {})
        resolved_key = resolve_existing_poll_key(guild_polls, poll_name)
        if resolved_key is None:
            return None, poll_key
        poll = guild_polls.get(resolved_key)
        if poll is None:
            return None, poll_key
        return json.loads(json.dumps(poll)), resolved_key


async def set_poll_use_event_sessions(
    guild_id: int,
    poll_name: str,
    use_event_sessions: bool,
) -> tuple[dict | None, str | None]:
    guild_key = str(guild_id)
    async with moomle_lock:
        guild_polls = moomle_polls.get(guild_key, {})
        resolved_key = resolve_existing_poll_key(guild_polls, poll_name)
        if resolved_key is None:
            return None, None

        poll = guild_polls.get(resolved_key)
        if poll is None:
            return None, resolved_key

        poll["use_event_sessions"] = use_event_sessions
        guild_polls[resolved_key] = poll
        save_moomle_polls_to_disk(moomle_polls)
        return json.loads(json.dumps(poll)), resolved_key


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

moomle_polls = load_moomle_polls_from_disk()

