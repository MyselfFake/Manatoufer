import os
import re
import random
import asyncio
import threading
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from discord import app_commands
from discord.ext import commands

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

intents = discord.Intents.default()
intents.members = True
intents.reactions = True
intents.messages = True
intents.message_content = False

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

# message_id -> role_name
active_events = {}
# event_key -> {"channel_id": int, "role_name": str}
event_resources = {}
# event_key -> asyncio.Lock (protege la creation locale contre la concurrence)
event_setup_locks: dict[str, asyncio.Lock] = {}
moomle_polls: dict[str, dict[str, dict]] = {}
moomle_lock: asyncio.Lock = asyncio.Lock()

MOOMLE_STORAGE_FILE = "moomle_polls.json"
MAX_MOOMLE_SLOTS = 20
MAX_MOOMLE_SESSIONS = 25
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

commands_synced = False


def normalize_event_key(event_name: str) -> str:
    return event_name.strip().lower()


def with_mm_event_prefix(name: str) -> str:
    cleaned = name.strip()
    if cleaned.lower().startswith(MM_EVENT_PREFIX):
        return cleaned
    return f"{MM_EVENT_PREFIX}{cleaned}"


def normalize_event_category_name(name: str) -> str:
    normalized = name.upper()
    normalized = (
        normalized.replace("É", "E")
        .replace("È", "E")
        .replace("Ê", "E")
        .replace("Ë", "E")
        .replace("À", "A")
        .replace("Â", "A")
        .replace("Î", "I")
        .replace("Ï", "I")
        .replace("Ô", "O")
        .replace("Û", "U")
        .replace("Ü", "U")
    )
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def is_event_category_name(name: str) -> bool:
    normalized = normalize_event_category_name(name)
    return "PLANIFICATION" in normalized and "STRATEGIQUE" in normalized


def find_event_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    category = discord.utils.get(guild.categories, name=EVENT_CATEGORY_NAME)
    if category is not None:
        return category

    candidates = [candidate for candidate in guild.categories if is_event_category_name(candidate.name)]
    if not candidates:
        return None

    candidates.sort(key=lambda candidate: (-len(candidate.text_channels), candidate.id))
    return candidates[0]


def to_valid_channel_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-_]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name or "event"


def build_event_channel_name(event_name: str, emoji: str) -> str:
    return f"{emoji}|{with_mm_event_prefix(event_name)}"


def extract_emoji_from_channel_name(channel_name: str) -> str | None:
    if "|" not in channel_name:
        return None
    prefix = channel_name.split("|", 1)[0].strip()
    if prefix.lower().startswith(MM_EVENT_PREFIX):
        prefix = prefix[len(MM_EVENT_PREFIX):].strip()
    return prefix or None


def build_event_role_name(event_name: str, emoji: str) -> str:
    return f"{emoji} {with_mm_event_prefix(event_name)}"


def extract_event_name_from_role_name(role_name: str) -> str:
    parts = role_name.split(" ", 1)
    if len(parts) == 2:
        return parts[1].strip()
    return role_name.strip()


def extract_emoji_from_role_name(role_name: str) -> str | None:
    parts = role_name.split(" ", 1)
    if len(parts) == 2:
        emoji = parts[0].strip()
        if emoji.lower().startswith(MM_EVENT_PREFIX):
            emoji = emoji[len(MM_EVENT_PREFIX):].strip()
        return emoji or None
    return None


def pick_default_event_emoji(event_name: str) -> str:
    event_key = normalize_event_key(event_name)
    if not EVENT_CHANNEL_EMOJIS:
        return "ðŸŽ¯"
    score = sum(ord(ch) for ch in event_key)
    return EVENT_CHANNEL_EMOJIS[score % len(EVENT_CHANNEL_EMOJIS)]


def get_event_setup_lock(event_key: str) -> asyncio.Lock:
    lock = event_setup_locks.get(event_key)
    if lock is None:
        lock = asyncio.Lock()
        event_setup_locks[event_key] = lock
    return lock


def find_event_role(guild: discord.Guild, event_name: str) -> discord.Role | None:
    base_name = event_name.strip().lower()
    prefixed_name = with_mm_event_prefix(event_name).lower()
    suffixes = {f" {base_name}", f" {prefixed_name}"}
    exact_names = {base_name, prefixed_name}

    for role in guild.roles:
        role_name = role.name.lower()
        if any(role_name.endswith(suffix) for suffix in suffixes):
            return role

    for role in guild.roles:
        if role.name.lower() in exact_names:
            return role

    return None


def find_event_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel | None,
    event_name: str,
    role: discord.Role | None = None,
) -> discord.TextChannel | None:
    target_event = event_name.lower()
    prefixed_event = with_mm_event_prefix(event_name).lower()
    safe_event_tail = to_valid_channel_name(event_name)
    safe_prefixed_tail = to_valid_channel_name(with_mm_event_prefix(event_name))
    best_match = None
    best_score = -1

    for channel in guild.text_channels:
        channel_name = channel.name.lower()
        channel_safe = to_valid_channel_name(channel_name.replace("|", "-"))
        score = 0
        has_event_match = False

        if category is not None and channel.category and channel.category.id == category.id:
            score += 5
        if channel_name.endswith(f"|{target_event}") or channel_name.endswith(f"|{prefixed_event}"):
            score += 6
            has_event_match = True
        if channel_name.endswith(f"|{safe_event_tail}") or channel_name.endswith(f"|{safe_prefixed_tail}"):
            score += 5
            has_event_match = True
        if (
            channel_name.endswith(f"-{safe_event_tail}")
            or channel_name == safe_event_tail
            or channel_name.endswith(f"-{safe_prefixed_tail}")
            or channel_name == safe_prefixed_tail
        ):
            score += 4
            has_event_match = True
        if channel_safe.endswith(safe_event_tail) or channel_safe.endswith(safe_prefixed_tail):
            score += 2
            has_event_match = True
        if role is not None and role in channel.overwrites:
            score += 3
            has_event_match = True

        if not has_event_match:
            continue

        if score > best_score:
            best_match = channel
            best_score = score

    if best_score <= 0:
        return None
    return best_match


def find_event_channel_for_role_name(guild: discord.Guild, role_name: str) -> discord.TextChannel | None:
    # Priorite: mapping exact cree au moment du /event ou !event.
    for tracked in event_resources.values():
        if tracked.get("role_name") != role_name:
            continue
        tracked_channel = guild.get_channel(tracked.get("channel_id"))
        if isinstance(tracked_channel, discord.TextChannel):
            return tracked_channel

    # Fallback: deduire le nom de l'event depuis le role.
    event_name = extract_event_name_from_role_name(role_name)
    role = discord.utils.get(guild.roles, name=role_name)
    category = find_event_category(guild)
    return find_event_channel(guild, category, event_name, role=role)


def build_private_channel_overwrites(guild: discord.Guild, role: discord.Role) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        )

    return overwrites


def resolve_event_entities(guild: discord.Guild, event_name: str) -> tuple[discord.TextChannel | None, discord.Role | None, str]:
    event_key = normalize_event_key(event_name)
    tracked = event_resources.get(event_key)

    category = find_event_category(guild)
    event_channel = None
    role = None

    if tracked is not None:
        tracked_channel = guild.get_channel(tracked.get("channel_id"))
        if isinstance(tracked_channel, discord.TextChannel):
            event_channel = tracked_channel
        tracked_role_name = tracked.get("role_name")
        if isinstance(tracked_role_name, str):
            role = discord.utils.get(guild.roles, name=tracked_role_name)

    event_emoji = extract_emoji_from_channel_name(event_channel.name) if event_channel else None

    if role is None and event_emoji:
        role = discord.utils.get(guild.roles, name=build_event_role_name(event_name, event_emoji))
    if role is None:
        role = find_event_role(guild, event_name)
    if event_channel is None:
        event_channel = find_event_channel(guild, category, event_name, role=role)

    return event_channel, role, event_key


def cleanup_event_tracking(event_name: str, deleted_role_name: str | None):
    event_name_lower = event_name.lower()
    event_key = normalize_event_key(event_name)
    for message_id, mapped_role_name in list(active_events.items()):
        mapped_role_lower = mapped_role_name.lower()
        if (
            (deleted_role_name is not None and mapped_role_name == deleted_role_name)
            or mapped_role_lower == event_name_lower
            or mapped_role_lower.endswith(f" {event_name_lower}")
        ):
            active_events.pop(message_id, None)

    event_resources.pop(event_key, None)
    lock = event_setup_locks.get(event_key)
    if lock is not None and not lock.locked():
        event_setup_locks.pop(event_key, None)


async def ensure_event_setup(guild: discord.Guild, event_name: str) -> tuple[discord.TextChannel, discord.Role, str]:
    event_key = normalize_event_key(event_name)
    lock = get_event_setup_lock(event_key)

    async with lock:
        category = find_event_category(guild)
        if category is None:
            category = await guild.create_category(EVENT_CATEGORY_NAME)
            print(f"Categorie '{EVENT_CATEGORY_NAME}' creee !")
        elif category.name != EVENT_CATEGORY_NAME:
            try:
                previous_category_name = category.name
                await category.edit(name=EVENT_CATEGORY_NAME)
                print(f"Categorie '{previous_category_name}' renommee en '{EVENT_CATEGORY_NAME}'.")
            except discord.HTTPException:
                pass

        event_channel = find_event_channel(guild, category, event_name)

        event_emoji = extract_emoji_from_channel_name(event_channel.name) if event_channel else None
        if not event_emoji or event_emoji not in EVENT_CHANNEL_EMOJIS:
            event_emoji = pick_default_event_emoji(event_name)

        role_name = build_event_role_name(event_name, event_emoji)
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            legacy_role = discord.utils.get(guild.roles, name=event_name)
            if legacy_role is not None:
                try:
                    await legacy_role.edit(name=role_name)
                    role = legacy_role
                    print(f"Role '{event_name}' renomme en '{role_name}' !")
                except discord.HTTPException:
                    role = None

        # Evite de creer des doublons quand un role d'event existe deja
        # avec un autre emoji en prefixe.
        if role is None:
            role = find_event_role(guild, event_name)
            if role is not None:
                role_name = role.name
                role_emoji = extract_emoji_from_role_name(role_name)
                if role_emoji and role_emoji in EVENT_CHANNEL_EMOJIS:
                    event_emoji = role_emoji

        # Rafraichit une fois depuis l'API avant creation: utile en multi-instance.
        if role is None:
            try:
                remote_roles = await guild.fetch_roles()
                role = next((r for r in remote_roles if r.name == role_name), None)
                if role is None:
                    target_name = event_name.strip().lower()
                    target_suffix = f" {target_name}"
                    role = next(
                        (
                            r
                            for r in remote_roles
                            if r.name.lower().endswith(target_suffix) or r.name.lower() == target_name
                        ),
                        None,
                    )
                    if role is not None:
                        role_name = role.name
                        role_emoji = extract_emoji_from_role_name(role_name)
                        if role_emoji and role_emoji in EVENT_CHANNEL_EMOJIS:
                            event_emoji = role_emoji
            except discord.HTTPException:
                pass

        target_role_name = build_event_role_name(event_name, event_emoji)
        if role is not None:
            if role.name != target_role_name:
                try:
                    previous_role_name = role.name
                    await role.edit(name=target_role_name)
                    role_name = target_role_name
                    print(f"Role '{previous_role_name}' renomme en '{target_role_name}' !")
                except discord.HTTPException:
                    role_name = role.name
            else:
                role_name = target_role_name
        else:
            role_name = target_role_name

        if role is None:
            role = await guild.create_role(
                name=role_name,
                color=discord.Color.random(),
            )
            print(f"Role '{role_name}' cree !")

        overwrites = build_private_channel_overwrites(guild, role)

        requested_channel_name = build_event_channel_name(event_name, event_emoji)
        if event_channel is None:
            # Recheck juste avant creation (cas d'une autre instance qui vient de finir).
            event_channel = find_event_channel(guild, category, event_name, role=role)

        if event_channel is None:
            try:
                event_channel = await guild.create_text_channel(
                    name=requested_channel_name,
                    category=category,
                    overwrites=overwrites,
                )
                print(f"Salon prive '{event_channel.name}' cree dans la categorie cible !")
                await event_channel.send(EVENT_CHANNEL_WELCOME_MESSAGE)
            except discord.HTTPException:
                safe_channel_name = to_valid_channel_name(requested_channel_name.replace("|", "-"))
                event_channel = next(
                    (
                        channel
                        for channel in guild.text_channels
                        if channel.category
                        and channel.category.id == category.id
                        and (channel.name == safe_channel_name or channel.name == requested_channel_name)
                    ),
                    None,
                )
                if event_channel is None:
                    event_channel = find_event_channel(guild, category, event_name, role=role)

                if event_channel is None:
                    event_channel = await guild.create_text_channel(
                        name=safe_channel_name,
                        category=category,
                        overwrites=overwrites,
                    )
                    print(f"Salon prive '{event_channel.name}' cree (nom adapte Discord).")
                    await event_channel.send(EVENT_CHANNEL_WELCOME_MESSAGE)
                else:
                    await event_channel.edit(category=category, overwrites=overwrites)
        else:
            try:
                await event_channel.edit(
                    name=requested_channel_name,
                    category=category,
                    overwrites=overwrites,
                )
            except discord.HTTPException:
                await event_channel.edit(category=category, overwrites=overwrites)

        event_resources[event_key] = {
            "channel_id": event_channel.id,
            "role_name": role_name,
        }

        return event_channel, role, role_name


async def register_event_message(message: discord.Message, role_name: str):
    active_events[message.id] = role_name
    print(f"Evenement actif enregistre (message_id={message.id}, role={role_name}).")


async def delete_event_resources(
    guild: discord.Guild,
    event_name: str,
    actor: str,
) -> list[str] | None:
    event_channel, role, _ = resolve_event_entities(guild, event_name)
    if event_channel is None and role is None:
        return None

    deleted_labels = []
    deleted_role_name = role.name if role is not None else None

    if event_channel is not None:
        deleted_labels.append(f"salon `{event_channel.name}`")
        await event_channel.delete(reason=f"Suppression event '{event_name}' par {actor}")

    if role is not None:
        deleted_labels.append(f"role `{role.name}`")
        await role.delete(reason=f"Suppression event '{event_name}' par {actor}")

    cleanup_event_tracking(event_name, deleted_role_name)
    return deleted_labels


def get_moomle_storage_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, MOOMLE_STORAGE_FILE)


def load_moomle_polls_from_disk() -> dict[str, dict[str, dict]]:
    path = get_moomle_storage_path()
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Erreur chargement moomle ({path}): {error}")
        return {}

    if not isinstance(payload, dict):
        return {}

    sanitized: dict[str, dict[str, dict]] = {}
    for guild_id, polls in payload.items():
        if not isinstance(guild_id, str) or not isinstance(polls, dict):
            continue

        sanitized_polls: dict[str, dict] = {}
        for poll_key, poll_data in polls.items():
            if isinstance(poll_key, str) and isinstance(poll_data, dict):
                sanitized_polls[poll_key] = poll_data

        if sanitized_polls:
            sanitized[guild_id] = sanitized_polls

    return sanitized


def save_moomle_polls_to_disk(payload: dict[str, dict[str, dict]]):
    path = get_moomle_storage_path()
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
    except OSError as error:
        print(f"Erreur sauvegarde moomle ({path}): {error}")


def normalize_poll_key(name: str) -> str:
    return normalize_event_key(name)


def build_slot_emoji_to_index(slots: list[str]) -> dict[str, str]:
    max_len = min(len(slots), len(MOOMLE_SLOT_REACTION_EMOJIS))
    mapping = {}
    for index in range(max_len):
        mapping[MOOMLE_SLOT_REACTION_EMOJIS[index]] = str(index + 1)
    return mapping


def render_slot_lines_with_emojis(slots: list[str]) -> list[str]:
    lines = []
    for index, slot_label in enumerate(slots, start=1):
        emoji = MOOMLE_SLOT_REACTION_EMOJIS[index - 1] if index - 1 < len(MOOMLE_SLOT_REACTION_EMOJIS) else "•"
        lines.append(f"{emoji} {index}. {slot_label}")
    return lines


def find_poll_by_message_id(guild_polls: dict[str, dict], message_id: int) -> tuple[str, dict] | tuple[None, None]:
    for poll_key, poll in guild_polls.items():
        if poll.get("message_id") == message_id:
            return poll_key, poll
    return None, None


def parse_semicolon_values(raw_value: str) -> list[str]:
    values = []
    for chunk in raw_value.split(";"):
        value = chunk.strip()
        if value:
            values.append(value)
    return values


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
        save_moomle_polls_to_disk(moomle_polls)

    return True


moomle_polls = load_moomle_polls_from_disk()


class DeleteConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut confirmer.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger, emoji=DELETE_CONFIRM_EMOJI)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, emoji=DELETE_CANCEL_EMOJI)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


@bot.event
async def on_ready():
    global commands_synced

    print(f"Bot connecte en tant que {bot.user} !")

    if commands_synced:
        return

    try:
        if GUILD_ID is not None:
            synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print(f"Slash commands sync (guild {GUILD_ID}): {len(synced)}")
        else:
            for guild in bot.guilds:
                try:
                    # Nettoie les anciennes commandes "guild-scoped" pour eviter les doublons
                    # quand des commandes globales existent aussi.
                    bot.tree.clear_commands(guild=guild)
                    await bot.tree.sync(guild=guild)
                except Exception as guild_error:
                    print(f"Echec nettoyage slash commands guild {guild.id}: {guild_error}")
            synced = await bot.tree.sync()
            print(f"Slash commands sync globaux: {len(synced)}")
        commands_synced = True
    except Exception as e:
        print(f"Erreur sync slash commands: {e}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        if bot.user and payload.user_id == bot.user.id:
            return

        moomle_handled = await handle_moomle_reaction_vote(payload, is_add=True)
        if moomle_handled:
            return

        if str(payload.emoji) != CHECK_EMOJI:
            return

        role_name = active_events.get(payload.message_id)
        if role_name is None:
            return

        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = payload.member
        if member is None:
            member = guild.get_member(payload.user_id)
            if member is None:
                member = await guild.fetch_member(payload.user_id)

        if member.bot:
            return

        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            role = await guild.create_role(
                name=role_name,
                color=discord.Color.random(),
            )
            print(f"Role '{role_name}' cree !")

        await member.add_roles(role)
        print(f"Role '{role_name}' attribue a {member.display_name} !")

        event_channel = find_event_channel_for_role_name(guild, role_name)
        if event_channel is not None:
            await event_channel.send(
                f"{member.mention} a rejoint l'event.",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

    except Exception as e:
        print(f"Erreur (ajout de role): {e}")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    try:
        if bot.user and payload.user_id == bot.user.id:
            return

        moomle_handled = await handle_moomle_reaction_vote(payload, is_add=False)
        if moomle_handled:
            return

        if str(payload.emoji) != CHECK_EMOJI:
            return

        role_name = active_events.get(payload.message_id)
        if role_name is None:
            print("Aucun evenement actif pour cet emoji.")
            return

        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            return

        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            member = await guild.fetch_member(payload.user_id)

        if member.bot:
            return

        await member.remove_roles(role)
        print(f"Role '{role_name}' retire a {member.display_name} !")

    except Exception as e:
        print(f"Erreur (retrait de role): {e}")


@bot.tree.command(name="event", description="Cree un event (role + salon prive).")
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
        print(f"Erreur slash /event : {e}")
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


@bot.tree.command(name="delete", description="Supprime un event (role + salon) avec confirmation.")
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

        to_delete = []
        if event_channel is not None:
            to_delete.append(f"salon `{event_channel.name}`")
        if role is not None:
            to_delete.append(f"role `{role.name}`")

        view = DeleteConfirmView(author_id=interaction.user.id)
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
        print(f"Erreur slash /delete : {e}")
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


@bot.tree.command(name="moomle_create", description="Cree un sondage de disponibilites (sessions detectees automatiquement).")
@app_commands.rename(poll_name="periode", slots="date")
@app_commands.describe(
    poll_name="Periode (exemple: campagne-avril)",
    slots="Date(s) separee(s) par ; (ex: 2026-04-20 20:00;2026-04-23 20:00)",
)
async def moomle_create_slash(
    interaction: discord.Interaction,
    poll_name: str,
    slots: str,
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

        if not poll_key:
            await interaction.response.send_message("Le nom du sondage est vide.", ephemeral=True)
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
            }
            save_moomle_polls_to_disk(moomle_polls)

        session_labels = []
        for role_id in role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None:
                session_labels.append(f"`{get_session_display_name(role.name)}`")

        slot_lines = render_slot_lines_with_emojis(parsed_slots)
        embed = discord.Embed(
            title=f"Sondage moomle: {poll_name.strip()}",
            description=(
                "Sessions detectees automatiquement depuis tes events (si disponibles).\n"
                "Votez en reagissant avec les lettres en bas du message."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Sessions", value=", ".join(session_labels) if session_labels else "Aucune", inline=False)
        embed.add_field(name="Creneaux", value="\n".join(slot_lines)[:1024], inline=False)
        embed.set_footer(text="Puis lancez /moomle_suggest pour proposer automatiquement les sessions.")

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
        print(f"Erreur slash /moomle_create : {error}")
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
        respondents = {int(user_id) for user_id in votes.keys() if str(user_id).isdigit()}

        session_names = []
        for role_id in poll.get("session_role_ids", []):
            role = interaction.guild.get_role(role_id)
            if role is not None:
                session_names.append(f"`{get_session_display_name(role.name)}`")

        lines = []
        for index, slot_label in enumerate(slots, start=1):
            slot_key = str(index)
            slot_emoji = MOOMLE_SLOT_REACTION_EMOJIS[index - 1] if index - 1 < len(MOOMLE_SLOT_REACTION_EMOJIS) else "•"
            yes_ids = [
                int(user_id)
                for user_id, user_votes in votes.items()
                if str(user_id).isdigit() and user_votes.get(slot_key) is True
            ]
            yes_mentions = ", ".join(f"<@{user_id}>" for user_id in yes_ids[:8])
            if len(yes_ids) > 8:
                yes_mentions += ", ..."
            if not yes_mentions:
                yes_mentions = "personne"
            lines.append(f"{slot_emoji} {index}. {slot_label} -> {len(yes_ids)} dispo ({yes_mentions})")

        embed = discord.Embed(
            title=f"Etat moomle: {poll.get('name', poll_name)}",
            color=discord.Color.green(),
            description=f"Repondants: **{len(respondents)}**",
        )
        embed.add_field(name="Sessions cibles", value=", ".join(session_names) if session_names else "Aucune", inline=False)
        embed.add_field(name="Creneaux", value="\n".join(lines)[:1024] if lines else "Aucun", inline=False)

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


@bot.tree.command(name="moomle_delete", description="Supprime un sondage moomle.")
@app_commands.rename(poll_name="periode")
@app_commands.describe(poll_name="Periode")
async def moomle_delete_slash(interaction: discord.Interaction, poll_name: str):
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
        print(f"Erreur slash /moomle_delete : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant la suppression du moomle.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant la suppression du moomle.",
                ephemeral=True,
            )


@bot.tree.command(
    name="moomle_suggest",
    description="Propose automatiquement les sessions qui matchent les disponibilites.",
)
@app_commands.rename(poll_name="periode")
@app_commands.describe(poll_name="Periode")
async def moomle_suggest_slash(interaction: discord.Interaction, poll_name: str):
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
        respondents: set[int] = {int(user_id) for user_id in votes.keys() if str(user_id).isdigit()}

        if len(respondents) == 0:
            await interaction.response.send_message(
                "Aucun vote enregistre pour l'instant.",
                ephemeral=True,
            )
            return

        candidate_roles_by_id: dict[int, discord.Role] = {}
        for role in list_moomle_session_roles(interaction.guild):
            candidate_roles_by_id[role.id] = role
        for role_id in poll.get("session_role_ids", []):
            role = interaction.guild.get_role(role_id)
            if role is not None:
                candidate_roles_by_id[role.id] = role

        sessions = []
        for role in candidate_roles_by_id.values():
            role_member_ids = {member.id for member in role.members if not member.bot}
            required_user_ids = role_member_ids & respondents
            if len(required_user_ids) == 0:
                continue

            sessions.append(
                {
                    "role_id": role.id,
                    "role_name": role.name,
                    "required_user_ids": required_user_ids,
                }
            )

        if len(sessions) == 0:
            await interaction.response.send_message(
                "Aucun role de session detecte chez les personnes ayant repondu au sondage.",
                ephemeral=True,
            )
            return

        suggestion_lines = []
        for slot_index, slot_label in enumerate(slots, start=1):
            slot_key = str(slot_index)
            slot_emoji = (
                MOOMLE_SLOT_REACTION_EMOJIS[slot_index - 1]
                if slot_index - 1 < len(MOOMLE_SLOT_REACTION_EMOJIS)
                else "•"
            )
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

            if len(feasible_sessions) == 0:
                suggestion_lines.append(f"{slot_emoji} {slot_index}. {slot_label} -> aucune session")
                continue

            selected_sessions = pick_maximal_sessions(feasible_sessions)
            selected_sessions.sort(key=lambda session: (-len(session["required_user_ids"]), session["role_name"].lower()))

            rendered_sessions = []
            for session in selected_sessions:
                player_mentions = ", ".join(f"<@{user_id}>" for user_id in sorted(session["required_user_ids"]))
                rendered_sessions.append(
                    f"`{get_session_display_name(session['role_name'])}` ({len(session['required_user_ids'])} joueurs: {player_mentions})"
                )

            suggestion_lines.append(f"{slot_emoji} {slot_index}. {slot_label} -> " + " | ".join(rendered_sessions))

        embed = discord.Embed(
            title=f"Propositions auto: {poll.get('name', poll_name)}",
            description=(
                "Regle appliquee: on garde uniquement les sessions maximales (si une session plus large est possible, "
                "les sous-sessions sont ignorees)."
            ),
            color=discord.Color.gold(),
        )

        chunk = []
        chunk_length = 0
        for line in suggestion_lines:
            candidate = len(line) + 1
            if chunk_length + candidate > 1000 and chunk:
                embed.add_field(name="Resultats", value="\n".join(chunk), inline=False)
                chunk = [line]
                chunk_length = candidate
            else:
                chunk.append(line)
                chunk_length += candidate
        if chunk:
            embed.add_field(name="Resultats", value="\n".join(chunk), inline=False)

        await interaction.response.send_message(embed=embed)

    except Exception as error:
        print(f"Erreur slash /moomle_suggest : {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue pendant le calcul du moomle.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant le calcul du moomle.",
                ephemeral=True,
            )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"Erreur slash command: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Une erreur est survenue avec la commande slash.", ephemeral=True)
        else:
            await interaction.response.send_message("Une erreur est survenue avec la commande slash.", ephemeral=True)
    except (discord.InteractionResponded, discord.HTTPException):
        try:
            await interaction.followup.send("Une erreur est survenue avec la commande slash.", ephemeral=True)
        except discord.HTTPException:
            pass


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 19045))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


if os.environ.get("PORT"):
    threading.Thread(target=run_health_server, daemon=True).start()

if not TOKEN:
    raise RuntimeError(
        "Token Discord manquant. Definis la variable d'environnement DISCORD_TOKEN (ou TOKEN) avant de lancer le bot."
    )

bot.run(TOKEN)

