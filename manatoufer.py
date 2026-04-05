import os
import re
import random
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.environ.get("TOKEN")

GUILD_ID_RAW = os.environ.get("GUILD_ID")
GUILD_ID = int(GUILD_ID_RAW) if GUILD_ID_RAW and GUILD_ID_RAW.isdigit() else None

CHECK_EMOJI = "\u2705"
EVENT_CATEGORY_NAME = "━━━━━━━━━━ 🎯 PLANIFICATION STRATEGIQUE ━━━━━━━━━━"
EVENT_CHANNEL_EMOJIS = ["🎯", "📌", "🚀", "🔥", "⭐", "✅", "🧭", "📣", "🎉"]
DELETE_CONFIRM_EMOJI = "✅"
DELETE_CANCEL_EMOJI = "❌"
EVENT_CHANNEL_WELCOME_MESSAGE = "C'est ici que vous pouvez échanger et vous organiser pour cet évènement."

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

commands_synced = False


def normalize_event_key(event_name: str) -> str:
    return event_name.strip().lower()


def to_valid_channel_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-_]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name or "event"


def build_event_channel_name(event_name: str, emoji: str) -> str:
    return f"{emoji}|{event_name}"


def extract_emoji_from_channel_name(channel_name: str) -> str | None:
    if "|" not in channel_name:
        return None
    prefix = channel_name.split("|", 1)[0].strip()
    return prefix or None


def build_event_role_name(event_name: str, emoji: str) -> str:
    return f"{emoji} {event_name}"


def extract_event_name_from_role_name(role_name: str) -> str:
    parts = role_name.split(" ", 1)
    if len(parts) == 2:
        return parts[1].strip()
    return role_name.strip()


def extract_emoji_from_role_name(role_name: str) -> str | None:
    parts = role_name.split(" ", 1)
    if len(parts) == 2:
        emoji = parts[0].strip()
        return emoji or None
    return None


def pick_default_event_emoji(event_name: str) -> str:
    event_key = normalize_event_key(event_name)
    if not EVENT_CHANNEL_EMOJIS:
        return "🎯"
    score = sum(ord(ch) for ch in event_key)
    return EVENT_CHANNEL_EMOJIS[score % len(EVENT_CHANNEL_EMOJIS)]


def get_event_setup_lock(event_key: str) -> asyncio.Lock:
    lock = event_setup_locks.get(event_key)
    if lock is None:
        lock = asyncio.Lock()
        event_setup_locks[event_key] = lock
    return lock


def find_event_role(guild: discord.Guild, event_name: str) -> discord.Role | None:
    target_name = event_name.lower()
    target_suffix = f" {target_name}"

    for role in guild.roles:
        if role.name.lower().endswith(target_suffix):
            return role

    for role in guild.roles:
        if role.name.lower() == target_name:
            return role

    return None


def find_event_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel | None,
    event_name: str,
    role: discord.Role | None = None,
) -> discord.TextChannel | None:
    target_event = event_name.lower()
    safe_event_tail = to_valid_channel_name(event_name)
    best_match = None
    best_score = -1

    for channel in guild.text_channels:
        channel_name = channel.name.lower()
        channel_safe = to_valid_channel_name(channel_name.replace("|", "-"))
        score = 0
        has_event_match = False

        if category is not None and channel.category and channel.category.id == category.id:
            score += 5
        if channel_name.endswith(f"|{target_event}"):
            score += 6
            has_event_match = True
        if channel_name.endswith(f"|{safe_event_tail}"):
            score += 5
            has_event_match = True
        if channel_name.endswith(f"-{safe_event_tail}") or channel_name == safe_event_tail:
            score += 4
            has_event_match = True
        if channel_safe.endswith(safe_event_tail):
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
    category = discord.utils.get(guild.categories, name=EVENT_CATEGORY_NAME)
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

    category = discord.utils.get(guild.categories, name=EVENT_CATEGORY_NAME)
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
        category = discord.utils.get(guild.categories, name=EVENT_CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(EVENT_CATEGORY_NAME)
            print(f"Categorie '{EVENT_CATEGORY_NAME}' creee !")

        event_channel = find_event_channel(guild, category, event_name)

        event_emoji = extract_emoji_from_channel_name(event_channel.name) if event_channel else None
        if not event_emoji:
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
                if role_emoji:
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
                        if role_emoji:
                            event_emoji = role_emoji
            except discord.HTTPException:
                pass

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

bot.run(TOKEN)
