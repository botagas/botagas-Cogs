from typing import Any, Optional

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

        await interaction.response.send_modal(CaptchaModal(self.cog, self.user_id, self.expected_code))


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
            await dm.send(
                content="Please solve the captcha below and reply here with the code:",
                file=discord.File(image_fp)
            )
            await interaction.response.send_message(
                "📩 I've sent you a DM with your captcha. Please reply there.",
                ephemeral=True,
            )
        except discord.Forbidden:
            message = await interaction.response.send_message(
                content=(
                    "⚠️ I couldn't DM you — likely due to disabled DMs.\n"
                    "Solve the captcha below and click the button to submit."
                ),
                file=discord.File(image_fp),
                ephemeral=True,
                view=CaptchaSubmitView(self.cog, member.id, code)
            )
            try:
                await self.cog.config.guild(interaction.guild).captcha_message.set({
                    "channel_id": interaction.channel.id,
                    "message_id": message.id
                })
            except Exception:
                pass


# Patch these into core.py (example usage):
async def _initialize(self):
    await self.bot.wait_until_red_ready()
    await self._build_cache()

    for guild_id, data in self._config.items():
        if not data["toggle"]:
            continue

        try:
            captcha_info = await self.config.guild_from_id(guild_id).captcha_message()
        except Exception:
            continue

        if not captcha_info:
            continue

        guild = self.bot.get_guild(guild_id)
        if not guild:
            continue

        channel = guild.get_channel(captcha_info.get("channel_id"))
        if not isinstance(channel, discord.TextChannel):
            continue

        try:
            self.bot.add_view(CaptchaVerifyButton(self), message_id=captcha_info.get("message_id"))
        except Exception:
            pass
    await self.bot.wait_until_red_ready()
    await self._build_cache()

    for guild_id, data in self._config.items():
        if not data["toggle"]:
            continue

        captcha_info = await self.config.guild_from_id(guild_id).captcha_message()
        if not captcha_info:
            continue

        guild = self.bot.get_guild(guild_id)
        if not guild:
            continue

        channel = guild.get_channel(captcha_info["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            continue

        try:
            self.bot.add_view(CaptchaVerifyButton(self), message_id=captcha_info["message_id"])
        except Exception:
            pass


async def _on_captcha_failure(self, member: discord.abc.User, source: discord.Interaction | discord.Message):
    text = "❌ Incorrect captcha. Please try again or contact an admin."
    if isinstance(source, discord.Interaction):
        if source.response.is_done():
            await source.followup.send(text, ephemeral=True)
        else:
            await source.response.send_message(text, ephemeral=True)
    else:
        await source.channel.send(text)


async def _on_captcha_success(self, member: discord.Member, source: discord.Interaction | discord.Message):
    # Remove unverified role
    unverified_id = await self.config.guild(member.guild).role_before_captcha()
    unverified = member.guild.get_role(unverified_id) if unverified_id else None
    if unverified:
        try:
            await member.remove_roles(unverified, reason="Captcha passed - removing unverified role")
        except discord.Forbidden:
            pass

    # Add verified role
    role_id = await self.config.guild(member.guild).role_after_captcha()
    role = member.guild.get_role(role_id) if role_id else None
    if role:
        try:
            await member.add_roles(role, reason="Captcha passed")
        except discord.Forbidden:
            pass

    text = "✅ You passed the captcha! Welcome."
    if isinstance(source, discord.Interaction):
        if source.response.is_done():
            await source.followup.send(text, ephemeral=True)
        else:
            await source.response.send_message(text, ephemeral=True)
    else:
        await source.channel.send(text)
