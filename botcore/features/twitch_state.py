import os

from botcore.config import TWITCH_STORAGE_FILE
from botcore.storage import load_json_mapping, save_json_mapping


twitch_notifications: dict[str, dict[str, dict]] = {}


def get_twitch_storage_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, TWITCH_STORAGE_FILE)


def load_twitch_notifications_from_disk() -> dict[str, dict[str, dict]]:
    path = get_twitch_storage_path()
    payload = load_json_mapping(path)
    if payload:
        return payload
    if os.path.exists(path):
        print(f"Erreur chargement twitch ({path}): donnees invalides.")
    return {}


def save_twitch_notifications_to_disk(payload: dict[str, dict[str, dict]]):
    path = get_twitch_storage_path()
    if not save_json_mapping(path, payload):
        print(f"Erreur sauvegarde twitch ({path}).")


def reload_twitch_notifications_from_disk() -> dict[str, dict[str, dict]]:
    global twitch_notifications
    twitch_notifications = load_twitch_notifications_from_disk()
    return twitch_notifications


def normalize_twitch_login(channel_name: str) -> str:
    cleaned = channel_name.strip().lower()
    cleaned = cleaned.removeprefix("https://www.twitch.tv/")
    cleaned = cleaned.removeprefix("https://twitch.tv/")
    cleaned = cleaned.removeprefix("www.twitch.tv/")
    cleaned = cleaned.removeprefix("twitch.tv/")
    return cleaned.strip("/")


def build_twitch_notification_key(twitch_login: str, channel_id: int, role_id: int) -> str:
    return f"{twitch_login}:{channel_id}:{role_id}"


def upsert_twitch_notification(
    guild_id: int,
    twitch_login: str,
    channel_id: int,
    role_id: int,
    broadcaster_id: str | None,
    display_name: str,
    is_live: bool,
    last_stream_id: str | None,
):
    reload_twitch_notifications_from_disk()
    guild_key = str(guild_id)
    guild_notifications = twitch_notifications.setdefault(guild_key, {})
    notification_key = build_twitch_notification_key(twitch_login, channel_id, role_id)
    guild_notifications[notification_key] = {
        "twitch_login": twitch_login,
        "channel_id": channel_id,
        "role_id": role_id,
        "broadcaster_id": broadcaster_id,
        "display_name": display_name,
        "is_live": is_live,
        "last_stream_id": last_stream_id,
    }
    save_twitch_notifications_to_disk(twitch_notifications)


def toggle_twitch_notification(
    guild_id: int,
    twitch_login: str,
    channel_id: int,
    role_id: int,
    broadcaster_id: str | None,
    display_name: str,
    is_live: bool,
    last_stream_id: str | None,
) -> bool:
    reload_twitch_notifications_from_disk()
    guild_key = str(guild_id)
    guild_notifications = twitch_notifications.setdefault(guild_key, {})
    notification_key = build_twitch_notification_key(twitch_login, channel_id, role_id)

    existing = guild_notifications.get(notification_key)
    if isinstance(existing, dict):
        guild_notifications.pop(notification_key, None)
        if not guild_notifications:
            twitch_notifications.pop(guild_key, None)
        save_twitch_notifications_to_disk(twitch_notifications)
        return False

    matching_key = None
    for candidate_key, candidate in guild_notifications.items():
        if not isinstance(candidate, dict):
            continue
        if candidate.get("twitch_login") != twitch_login:
            continue
        if candidate.get("channel_id") != channel_id:
            continue
        if candidate.get("role_id") != role_id:
            continue
        matching_key = candidate_key
        break

    if matching_key is not None:
        guild_notifications.pop(matching_key, None)
        if not guild_notifications:
            twitch_notifications.pop(guild_key, None)
        save_twitch_notifications_to_disk(twitch_notifications)
        return False

    guild_notifications[notification_key] = {
        "twitch_login": twitch_login,
        "channel_id": channel_id,
        "role_id": role_id,
        "broadcaster_id": broadcaster_id,
        "display_name": display_name,
        "is_live": is_live,
        "last_stream_id": last_stream_id,
    }
    save_twitch_notifications_to_disk(twitch_notifications)
    return True


def update_twitch_notification_state(
    guild_id: int,
    twitch_login: str,
    is_live: bool,
    last_stream_id: str | None,
):
    guild_notifications = twitch_notifications.get(str(guild_id), {})
    for notification_key, notification in list(guild_notifications.items()):
        if not isinstance(notification, dict):
            continue
        if notification.get("twitch_login") != twitch_login:
            continue
        notification["is_live"] = is_live
        notification["last_stream_id"] = last_stream_id
        guild_notifications[notification_key] = notification
    save_twitch_notifications_to_disk(twitch_notifications)


twitch_notifications = load_twitch_notifications_from_disk()
