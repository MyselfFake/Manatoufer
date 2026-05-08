import discord


def build_slot_emoji_to_index(slots: list[str], reaction_emojis: list[str]) -> dict[str, str]:
    max_len = min(len(slots), len(reaction_emojis))
    mapping = {}
    for index in range(max_len):
        mapping[reaction_emojis[index]] = str(index + 1)
    return mapping


def render_slot_lines_with_emojis(slots: list[str], reaction_emojis: list[str]) -> list[str]:
    lines = []
    for index, slot_label in enumerate(slots, start=1):
        emoji = reaction_emojis[index - 1] if index - 1 < len(reaction_emojis) else "•"
        lines.append(f"{emoji} {index}. {slot_label}")
    return lines


def parse_semicolon_values(raw_value: str) -> list[str]:
    values = []
    for chunk in raw_value.split(";"):
        value = chunk.strip()
        if value:
            values.append(value)
    return values


def build_moomle_poll_embed(
    poll_name: str,
    slots: list[str],
    session_labels: list[str],
    votes: dict[str, dict[str, bool]],
    end_at_ts: int | None,
    duration_hours: int | None,
    color: discord.Color,
    reaction_emojis: list[str],
    use_event_sessions: bool = True,
) -> discord.Embed:
    slot_lines = render_slot_lines_with_emojis(slots, reaction_emojis)
    respondents = [user_id for user_id in votes.keys() if str(user_id).isdigit()]
    total_possible_votes = len(slots)

    per_user_lines = []
    total_cast_votes = 0
    for user_id in respondents:
        user_votes = votes.get(user_id, {})
        vote_count = sum(1 for slot_key, has_voted in user_votes.items() if has_voted is True and slot_key.isdigit())
        total_cast_votes += vote_count
        per_user_lines.append((vote_count, f"<@{int(user_id)}> - {vote_count} vote(s)"))

    per_user_lines.sort(key=lambda item: (-item[0], item[1]))
    rendered_voters = [line for _, line in per_user_lines]
    voters_preview = "\n".join(rendered_voters[:20]) if rendered_voters else "Aucun vote pour le moment."
    if len(rendered_voters) > 20:
        voters_preview += f"\n... et {len(rendered_voters) - 20} autre(s)"

    avg_votes = (total_cast_votes / len(respondents)) if respondents else 0.0
    avg_percent = ((avg_votes / total_possible_votes) * 100.0) if total_possible_votes > 0 else 0.0
    avg_text = f"{avg_votes:.1f}".replace(".", ",")
    percent_text = f"{avg_percent:.0f}"

    if use_event_sessions:
        description = (
            "Sessions detectees automatiquement depuis tes events (si disponibles).\n"
            "Votez en reagissant avec les lettres en bas du message."
        )
    else:
        description = (
            "Mode sans events: les suggestions se basent uniquement sur les votants disponibles.\n"
            "Votez en reagissant avec les lettres en bas du message."
        )

    embed = discord.Embed(
        title=f"Sondage moomle: {poll_name}",
        description=description,
        color=color,
    )
    embed.add_field(name="Sessions", value=", ".join(session_labels) if session_labels else "Aucune", inline=False)
    embed.add_field(name="Creneaux", value="\n".join(slot_lines)[:1024] if slot_lines else "Aucun", inline=False)
    if end_at_ts is not None and duration_hours is not None:
        embed.add_field(
            name="Fin du sondage",
            value=f"Dans {duration_hours}h (fin: <t:{end_at_ts}:F>)",
            inline=False,
        )
    embed.add_field(
        name="Participation",
        value=(
            f"Repondants: **{len(respondents)}**\n"
            f"Moyenne de vote: **{avg_text} ({percent_text}%)** sur {total_possible_votes} possible(s)."
        ),
        inline=False,
    )
    embed.add_field(name="Votants (nb de votes)", value=voters_preview[:1024], inline=False)
    if use_event_sessions:
        embed.set_footer(text="Puis lancez /moomle_pool_suggest pour proposer automatiquement les sessions.")
    else:
        embed.set_footer(text="Puis lancez /moomle_pool_suggest pour proposer automatiquement les disponibilites.")
    return embed


def build_moomle_suggest_embed(
    poll: dict,
    suggestion_lines: list[str],
    is_automatic: bool,
    use_event_sessions: bool = True,
) -> discord.Embed:
    title_prefix = "Propositions auto (fin sondage): " if is_automatic else "Propositions auto: "
    if use_event_sessions:
        description = (
            "Regle appliquee: on garde uniquement les sessions maximales (si une session plus large est possible, "
            "les sous-sessions sont ignorees)."
        )
    else:
        description = "Mode sans events: chaque creneau affiche les votants disponibles ensemble."

    embed = discord.Embed(
        title=f"{title_prefix}{poll.get('name', 'Moomle')}",
        description=description,
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

    return embed
