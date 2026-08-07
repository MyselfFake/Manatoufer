import os
import discord


def load_local_env_file():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env")
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as error:
        print(f"Impossible de charger .env: {error}")


load_local_env_file()

TOKEN = (os.environ.get("DISCORD_TOKEN") or os.environ.get("TOKEN") or "").strip()

GUILD_ID_RAW = os.environ.get("GUILD_ID")
GUILD_ID = int(GUILD_ID_RAW) if GUILD_ID_RAW and GUILD_ID_RAW.isdigit() else None

CHECK_EMOJI = "\u2705"
EVENT_CATEGORY_NAME = (
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 "
    "\U0001F3AF PLANIFICATION STRATEGIQUE "
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
)
EVENT_CHANNEL_EMOJIS = [
    "\U0001F3AF",
    "\U0001F4CC",
    "\U0001F680",
    "\U0001F525",
    "\u2B50",
    "\u2705",
    "\U0001F9ED",
    "\U0001F4E3",
    "\U0001F389",
]
DELETE_CONFIRM_EMOJI = "\u2705"
DELETE_CANCEL_EMOJI = "\u274C"
EVENT_CHANNEL_WELCOME_MESSAGE = "C'est ici que vous pouvez echanger et vous organiser pour cet evenement."

MOOMLE_STORAGE_FILE = "moomle_polls.json"
MROLE_STORAGE_FILE = "mrole_reacts.json"
MAX_MOOMLE_SLOTS = 20
MAX_MOOMLE_SESSIONS = 25
MAX_MOOMLE_DURATION_HOURS = 720
MOOMLE_AUTO_SUGGEST_CHECK_SECONDS = 30
MM_EVENT_PREFIX = "mm_"
MOOMLE_SLOT_REACTION_EMOJIS = [
    "🇦",
    "🇧",
    "🇨",
    "🇩",
    "🇪",
    "🇫",
    "🇬",
    "🇭",
    "🇮",
    "🇯",
    "🇰",
    "🇱",
    "🇲",
    "🇳",
    "🇴",
    "🇵",
    "🇶",
    "🇷",
    "🇸",
    "🇹",
]


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True
    intents.reactions = True
    intents.messages = True
    intents.message_content = False
    return intents
