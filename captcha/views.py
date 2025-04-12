from typing import Any, Optional
from .format import format_message

import discord


class CaptchaModal(discord.ui.Modal, title="Captcha Verification"):
    def __init__(self, cog: Any, user_id: int, expected_code: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.expected_code = expected_code

        self.code_input = discord.ui.TextInput(
            label="Enter the text from the captcha image",
            placeholder="E.g. XDFQWE",
            required=True,
            max_length=6,
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        entered_code = self.code_input.value.strip().upper()
        self.cog.cleanup_captcha_image(self.user_id)
        if entered_code == self.expected_code:
            await self.cog._on_captcha_success(interaction.user, interaction)
        else:
            await self.cog._on_captcha_failure(interaction.user, interaction)


class CaptchaSubmitView(discord.ui.View):
    def __init__(self, cog: Any, user_id: int, expected_code: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.expected_code = expected_code

    @discord.ui.button(
        label="Submit Captcha", style=discord.ButtonStyle.primary, custom_id="captcha_modal_submit"
    )
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "This captcha isn't for you.", ephemeral=True
            )

        await interaction.response.send_modal(
            CaptchaModal(self.cog, self.user_id, self.expected_code)
        )


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
        if not guild_config["channel"]:
            return await interaction.response.send_message(
                "Verification channel not configured.", ephemeral=True
            )

        code = self.cog.generate_captcha_code()
        self.cog.register_active_challenge(member.id, code, interaction.guild.id)
        image_fp = self.cog.save_captcha_image(code, member.id)

        try:
            dm = await member.create_dm()
            message_before = await self.cog.config.guild(
                interaction.guild
            ).message_before_captcha()
            text = format_message(message_before, member)
            await dm.send(
                content=text,
                file=discord.File(image_fp),
            )
            await interaction.response.send_message(
                "📩 I've sent you a DM with your captcha. Please reply there.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                content=(
                    "⚠️ I couldn't DM you — likely due to disabled DMs.\n"
                    "Solve the captcha below and click the button to submit."
                ),
                file=discord.File(image_fp),  # NEW instance
                ephemeral=True,
                view=CaptchaSubmitView(self.cog, member.id, code),
            )
