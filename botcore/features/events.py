import asyncio
import re

import discord
from discord import app_commands

from botcore.config import (
    CHECK_EMOJI,
    DELETE_CANCEL_EMOJI,
    DELETE_CONFIRM_EMOJI,
    EVENT_CATEGORY_NAME,
    EVENT_CHANNEL_EMOJIS,
    EVENT_CHANNEL_WELCOME_MESSAGE,
    MM_EVENT_PREFIX,
)
from botcore.runtime import bot
from botcore.views import DeleteConfirmView

# message_id -> role_name
active_events = {}
# event_key -> {"channel_id": int, "role_name": str}
event_resources = {}
# event_key -> asyncio.Lock (protege la creation locale contre la concurrence)
event_setup_locks: dict[str, asyncio.Lock] = {}

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
    # Priorite: mapping exact cree au moment du /moomle_event_create.
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


async def rename_event_resources(
    guild: discord.Guild,
    old_event_name: str,
    new_event_name: str,
    actor: str,
) -> tuple[bool, str]:
    old_name = old_event_name.strip()
    new_name = new_event_name.strip()
    if not old_name or not new_name:
        return False, "Les noms d'event ne peuvent pas etre vides."

    event_channel, role, old_event_key = resolve_event_entities(guild, old_name)
    if event_channel is None and role is None:
        return False, f"Aucun event trouve pour `{old_name}`."

    new_event_key = normalize_event_key(new_name)
    if old_event_key != new_event_key:
        new_event_channel, new_role, _ = resolve_event_entities(guild, new_name)
        if new_event_channel is not None and (event_channel is None or new_event_channel.id != event_channel.id):
            return False, f"Un autre event existe deja pour `{new_name}` (salon detecte)."
        if new_role is not None and (role is None or new_role.id != role.id):
            return False, f"Un autre event existe deja pour `{new_name}` (role detecte)."

    previous_role_name = role.name if role is not None else None
    event_emoji = None
    if role is not None:
        event_emoji = extract_emoji_from_role_name(role.name)
    if event_emoji is None and event_channel is not None:
        event_emoji = extract_emoji_from_channel_name(event_channel.name)
    if event_emoji is None:
        event_emoji = pick_default_event_emoji(new_name)

    if role is not None:
        target_role_name = build_event_role_name(new_name, event_emoji)
        existing_target_role = discord.utils.get(guild.roles, name=target_role_name)
        if existing_target_role is not None and existing_target_role.id != role.id:
            return False, f"Impossible de renommer: le role `{target_role_name}` existe deja."
        if role.name != target_role_name:
            await role.edit(name=target_role_name, reason=f"Renommage event '{old_name}' par {actor}")

    if event_channel is not None:
        target_channel_name = build_event_channel_name(new_name, event_emoji)
        try:
            await event_channel.edit(name=target_channel_name, reason=f"Renommage event '{old_name}' par {actor}")
        except discord.HTTPException:
            safe_channel_name = to_valid_channel_name(target_channel_name.replace("|", "-"))
            await event_channel.edit(name=safe_channel_name, reason=f"Renommage event '{old_name}' par {actor}")

    final_role_name = role.name if role is not None else build_event_role_name(new_name, event_emoji)

    if previous_role_name is not None:
        for message_id, mapped_role_name in list(active_events.items()):
            if mapped_role_name == previous_role_name:
                active_events[message_id] = final_role_name

    tracked = event_resources.pop(old_event_key, None)
    tracked_channel_id = event_channel.id if event_channel is not None else None
    if tracked is not None and tracked_channel_id is None:
        tracked_channel_id = tracked.get("channel_id")
    event_resources[new_event_key] = {
        "channel_id": tracked_channel_id,
        "role_name": final_role_name,
    }

    if old_event_key != new_event_key:
        old_lock = event_setup_locks.get(old_event_key)
        if old_lock is not None and not old_lock.locked():
            event_setup_locks.pop(old_event_key, None)

    renamed_items = []
    if role is not None:
        renamed_items.append(f"role `{role.name}`")
    if event_channel is not None:
        renamed_items.append(f"salon `{event_channel.name}`")
    if not renamed_items:
        renamed_items.append("ressources")

    return True, f"Event renomme vers `{new_name}` ({', '.join(renamed_items)})."



@bot.tree.command(name="moomle_event_create", description="Cree un event (role + salon prive).")
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
        print(f"Erreur slash /moomle_event_create : {e}")
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


@bot.tree.command(name="moomle_event_change", description="Renomme un event (role + salon associes).")
@app_commands.describe(
    old_event_name="Nom actuel de l'event",
    new_event_name="Nouveau nom de l'event",
)
async def change_event_slash(
    interaction: discord.Interaction,
    old_event_name: str,
    new_event_name: str,
):
    try:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Cette commande doit etre utilisee sur un serveur.",
                ephemeral=True,
            )
            return

        old_name = old_event_name.strip()
        new_name = new_event_name.strip()
        if not old_name or not new_name:
            await interaction.response.send_message(
                "Les deux noms d'event sont obligatoires.",
                ephemeral=True,
            )
            return

        if normalize_event_key(old_name) == normalize_event_key(new_name):
            await interaction.response.send_message(
                "Le nouveau nom est identique au nom actuel.",
                ephemeral=True,
            )
            return

        success, result_message = await rename_event_resources(
            interaction.guild,
            old_name,
            new_name,
            str(interaction.user),
        )

        await interaction.response.send_message(result_message, ephemeral=True)

    except Exception as e:
        print(f"Erreur slash /moomle_event_change : {e}")
        if interaction.response.is_done():
            await interaction.followup.send(
                "Une erreur est survenue pendant le renommage de l'event.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Une erreur est survenue pendant le renommage de l'event.",
                ephemeral=True,
            )


@bot.tree.command(name="moomle_event_delete", description="Supprime un event (role + salon) avec confirmation.")
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

        view = DeleteConfirmView(
            author_id=interaction.user.id,
            confirm_emoji=DELETE_CONFIRM_EMOJI,
            cancel_emoji=DELETE_CANCEL_EMOJI,
        )
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
        print(f"Erreur slash /moomle_event_delete : {e}")
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


async def handle_event_reaction_add(payload: discord.RawReactionActionEvent):
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


async def handle_event_reaction_remove(payload: discord.RawReactionActionEvent):
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



