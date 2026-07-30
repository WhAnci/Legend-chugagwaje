import discord

class TopicChoiceView(discord.ui.View):
    def __init__(self, topics: list[str], owner_id: int, on_select):
        super().__init__(timeout=900)
        self.topics = topics
        self.owner_id = owner_id
        self.on_select = on_select
        for index, label in enumerate(["1", "2", "3"], 1):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"topic-{index}")
            button.callback = self._callback(index)
            self.add_item(button)
        again = discord.ui.Button(label="다시", style=discord.ButtonStyle.secondary, custom_id="topic-again")
        again.callback = self._again
        self.add_item(again)

    def _callback(self, index):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("이 선택지는 요청자만 사용할 수 있습니다.", ephemeral=True)
                return
            await interaction.response.defer()
            await self.on_select(interaction, self.topics[index - 1], self.topics)
        return callback

    async def _again(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("이 선택지는 요청자만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.on_select(interaction, None, self.topics)
