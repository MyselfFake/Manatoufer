import json
import time
import urllib.error
import urllib.parse
import urllib.request

from botcore.config import TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET


class TwitchApiError(Exception):
    pass


_cached_token: str | None = None
_cached_token_expires_at = 0


def has_twitch_credentials() -> bool:
    return bool(TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET)


def _read_json_request(request: urllib.request.Request, timeout: int = 15) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise TwitchApiError(f"Twitch HTTP {error.code}: {details}") from error
    except urllib.error.URLError as error:
        raise TwitchApiError(f"Twitch network error: {error}") from error

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise TwitchApiError("Twitch response JSON invalide.") from error
    if not isinstance(decoded, dict):
        raise TwitchApiError("Twitch response inattendue.")
    return decoded


def get_app_access_token() -> str:
    global _cached_token
    global _cached_token_expires_at

    if not has_twitch_credentials():
        raise TwitchApiError("Configuration Twitch manquante.")

    now = int(time.time())
    if _cached_token and _cached_token_expires_at - 60 > now:
        return _cached_token

    body = urllib.parse.urlencode(
        {
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://id.twitch.tv/oauth2/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    payload = _read_json_request(request)
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    if not isinstance(access_token, str) or not access_token:
        raise TwitchApiError("Token Twitch absent de la reponse.")

    _cached_token = access_token
    _cached_token_expires_at = now + int(expires_in)
    return access_token


def helix_get(path: str, query: dict[str, str]) -> dict:
    token = get_app_access_token()
    url = f"https://api.twitch.tv/helix/{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": TWITCH_CLIENT_ID,
        },
        method="GET",
    )
    return _read_json_request(request)


def get_twitch_user(login: str) -> dict | None:
    payload = helix_get("users", {"login": login})
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    user = data[0]
    return user if isinstance(user, dict) else None


def get_twitch_stream(login: str) -> dict | None:
    payload = helix_get("streams", {"user_login": login})
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    stream = data[0]
    return stream if isinstance(stream, dict) else None
