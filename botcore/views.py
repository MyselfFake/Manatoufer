import discord


class DeleteConfirmView(discord.ui.View):
    def __init__(self, author_id: int, confirm_emoji: str, cancel_emoji: str):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = False
        self.confirm_button.emoji = confirm_emoji
        self.cancel_button.emoji = cancel_emoji

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut confirmer.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.defer()

