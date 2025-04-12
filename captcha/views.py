# views.py
from typing import Optional

import discord


class CaptchaVerifyButton(discord.ui.View):
    def __init__(self, cog: Any, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    @discord.ui.button(
        label="Verify", style=discord.ButtonStyle.success, custom_id="captcha_verify"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user

        if member.bot:
            return

        guild_config = await self.cog.config.guild(interaction.guild).all()
        channel_id = guild_config["channel"]

        if not channel_id:
            return await interaction.response.send_message(
                "Verification channel not configured.", ephemeral=True
            )

        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Verification channel is invalid.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        await self.cog.begin_captcha_flow(member, channel)
