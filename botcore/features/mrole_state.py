import os

from botcore.config import MROLE_STORAGE_FILE
from botcore.storage import load_json_mapping, save_json_mapping


mrole_messages: dict[str, dict[str, dict]] = {}


def get_mrole_storage_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, MROLE_STORAGE_FILE)


def load_mrole_messages_from_disk() -> dict[str, dict[str, dict]]:
    path = get_mrole_storage_path()
    payload = load_json_mapping(path)
    if payload:
        return payload
    if os.path.exists(path):
        print(f"Erreur chargement mrole ({path}): donnees invalides.")
    return {}


def save_mrole_messages_to_disk(payload: dict[str, dict[str, dict]]):
    path = get_mrole_storage_path()
    if not save_json_mapping(path, payload):
        print(f"Erreur sauvegarde mrole ({path}).")


def register_mrole_message(guild_id: int, message_id: int, channel_id: int, emoji_roles: dict[str, int]):
    guild_key = str(guild_id)
    message_key = str(message_id)
    guild_messages = mrole_messages.setdefault(guild_key, {})
    guild_messages[message_key] = {
        "channel_id": channel_id,
        "roles": {emoji: role_id for emoji, role_id in emoji_roles.items()},
    }
    save_mrole_messages_to_disk(mrole_messages)


def get_mrole_role_id(guild_id: int, message_id: int, emoji: str) -> int | None:
    guild_messages = mrole_messages.get(str(guild_id), {})
    tracked = guild_messages.get(str(message_id), {})
    roles = tracked.get("roles")
    if not isinstance(roles, dict):
        return None

    role_id = roles.get(emoji)
    if isinstance(role_id, int):
        return role_id
    if isinstance(role_id, str) and role_id.isdigit():
        return int(role_id)
    return None


mrole_messages = load_mrole_messages_from_disk()
