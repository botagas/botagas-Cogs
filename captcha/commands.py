from typing import Any, Dict, Optional

import discord
import discord.app_commands as app_commands
from redbot.core import commands
from redbot.core.utils.chat_formatting import box
from redbot.core.utils.views import ConfirmView

from .abc import CompositeMetaClass, MixinMeta
from .format import format_message
from .views import CaptchaVerifyButton


class CaptchaCommands(MixinMeta, metaclass=CompositeMetaClass):
    captcha_group = app_commands.Group(name="captcha", description="Manage Captcha settings.")

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    @captcha_group.command(name="deploy", description="Deploy the verification message")
    @app_commands.default_permissions(administrator=True)
    async def deploy(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel_id = await self.config.guild(guild).channel()
        if not channel_id:
            return await interaction.response.send_message(
                "Verification channel not configured.", ephemeral=True
            )

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Invalid verification channel.", ephemeral=True
            )

        embed_text = await self.config.guild(guild).embed_text()
        embed = discord.Embed(
            description=format_message(embed_text, guild.me),
            color=discord.Color(0x34EB83),
        )

        view = CaptchaVerifyButton(self)
        msg = await channel.send(embed=embed, view=view)

        await self.config.guild(guild).set_raw("captcha_message", value={
            "channel_id": msg.channel.id,
            "message_id": msg.id,
        })

        await interaction.response.send_message("Verification message deployed.", ephemeral=True)

    @captcha_group.command(name="toggle", description="Enable or disable captcha verification")
    @app_commands.default_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, toggle: bool):
        guild = interaction.guild
        await self.config.guild(guild).toggle.set(toggle)
        await interaction.response.send_message(
            f"Captcha verification is now {'enabled' if toggle else 'disabled'}.", ephemeral=True
        )

    @captcha_group.command(
        name="unverifiedrole", description="Set the role assigned before captcha is completed."
    )
    @app_commands.default_permissions(administrator=True)
    async def unverifiedrole(self, interaction: discord.Interaction, role: Optional[discord.Role]):
        guild = interaction.guild
        if role is None:
            await self.config.guild(guild).role_before_captcha.clear()
            await interaction.response.send_message("Cleared the unverified role.", ephemeral=True)
            return
        await self.config.guild(guild).role_before_captcha.set(role.id)
        await interaction.response.send_message(
            f"Configured the unverified role to {role.name} ({role.id}).", ephemeral=True
        )

    @captcha_group.command(
        name="role", description="Set the role granted after captcha is completed."
    )
    @app_commands.default_permissions(administrator=True)
    async def role(self, interaction: discord.Interaction, role: Optional[discord.Role]):
        guild = interaction.guild
        if role is None:
            await self.config.guild(guild).role_after_captcha.clear()
            await interaction.response.send_message(
                "Cleared the captcha verification role.", ephemeral=True
            )
            return
        await self.config.guild(guild).role_after_captcha.set(role.id)
        await interaction.response.send_message(
            f"Configured the captcha verification role to {role.name} ({role.id}).", ephemeral=True
        )

    @captcha_group.command(
        name="timeout", description="Set the timeout for captcha verification (50–300 seconds)."
    )
    @app_commands.default_permissions(administrator=True)
    async def timeout(
        self, interaction: discord.Interaction, amount: app_commands.Range[int, 50, 300]
    ):
        guild = interaction.guild
        await self.config.guild(guild).timeout.set(amount)
        await interaction.response.send_message(
            f"Configured the timeout to {amount} seconds.", ephemeral=True
        )

    @captcha_group.command(
        name="tries", description="Set the max attempts allowed for captcha verification (2–10)."
    )
    @app_commands.default_permissions(administrator=True)
    async def tries(
        self, interaction: discord.Interaction, amount: app_commands.Range[int, 2, 10]
    ):
        guild = interaction.guild
        await self.config.guild(guild).tries.set(amount)
        await interaction.response.send_message(
            f"Configured the number of attempts to {amount}.", ephemeral=True
        )

    @captcha_group.command(name="before", description="Set the message shown before captcha.")
    @app_commands.default_permissions(administrator=True)
    async def before(self, interaction: discord.Interaction, message: str):
        guild = interaction.guild
        await self.config.guild(guild).message_before_captcha.set(message)
        await interaction.response.send_message(
            f"✅ Updated before-captcha message:\n{box(message, lang='yaml')}", ephemeral=True
        )


    @captcha_group.command(name="after", description="Set the message shown after captcha.")
    @app_commands.default_permissions(administrator=True)
    async def after(self, interaction: discord.Interaction, message: str):
        guild = interaction.guild
        await self.config.guild(guild).message_after_captcha.set(message)
        await interaction.response.send_message(
            f"✅ Updated after-captcha message:\n{box(message, lang='yaml')}", ephemeral=True
        )

    @captcha_group.command(name="embed", description="Click the green button below to verify")
    @app_commands.default_permissions(administrator=True)
    async def embed(self, interaction: discord.Interaction, message: Optional[str] = None):
        guild = interaction.guild

        if message is None:
            await self.config.guild(guild).embed_text.clear()
            await interaction.response.send_message("Cleared the embed message.", ephemeral=True)
            return

        await self.config.guild(guild).embed_text.set(message)
        await interaction.response.send_message(
            f"✅ Updated embed message:\n{box(message, lang='yaml')}", ephemeral=True
        )

        captcha_info = await self.config.guild(guild).get_raw("captcha_message", default=None)
        if not captcha_info:
            return  # No existing message to update

        try:
            channel = guild.get_channel(captcha_info["channel_id"])
            if not channel:
                raise RuntimeError("Configured channel not found.")
            msg = await channel.fetch_message(captcha_info["message_id"])

            embed = discord.Embed(
                description=format_message(message, guild.me),
                color=discord.Color(0x34EB83),
            )
            await msg.edit(embed=embed)
        except Exception as e:
            # Fallback if the embed update fails
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Embed updated, but I couldn't edit the deployed message: `{e}`",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"Embed updated, but I couldn't edit the deployed message: `{e}`",
                    ephemeral=True,
                )

    @captcha_group.command(name="settings", description="View the current captcha configuration.")
    @app_commands.default_permissions(administrator=True)
    async def settings(self, interaction: discord.Interaction):
        guild = interaction.guild
        data: Dict[str, Any] = await self.config.guild(guild).all()
        role = guild.get_role(data["role_after_captcha"])
        role = "None" if role is None else f"<@&{role.id}> ({role.id})"
        channel = guild.get_channel(data["channel"])
        channel = "None" if channel is None else f"<#{channel.id}> ({channel.id})"
        embed = discord.Embed(
            title="Captcha Settings",
            description=(
                f"**Toggle**: {data['toggle']}\n"
                f"**Channel**: {channel}\n"
                f"**Timeout**: {data['timeout']}\n"
                f"**Tries**: {data['tries']}\n"
                f"**Role**: {role}\n"
            ),
            color=discord.Color(0x34EB83),
        )
        embed.set_thumbnail(url=getattr(guild.icon, "url", None))
        embed.add_field(
            name="Before Captcha Message:",
            value=box(str(data["message_before_captcha"]), lang="json"),
            inline=False,
        )
        embed.add_field(
            name="Embed Text:",
            value=box(str(data["embed_text"]), lang="json"),
            inline=False,
        )
        embed.add_field(
            name="After Captcha Message:",
            value=box(str(data["message_after_captcha"]), lang="json"),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @captcha_group.command(name="channel", description="Set the channel for captcha verification.")
    @app_commands.default_permissions(administrator=True)
    async def channel(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]
    ):
        guild = interaction.guild
        if channel is None:
            await self.config.guild(guild).channel.clear()
            await interaction.response.send_message(
                "Cleared the captcha verification channel.", ephemeral=True
            )
            return
        await self.config.guild(guild).channel.set(channel.id)
        await interaction.response.send_message(
            f"Configured the captcha verification channel to {channel.name} ({channel.id}).",
            ephemeral=True,
        )

    @captcha_group.command(name="reset", description="Reset all captcha settings to default.")
    @app_commands.default_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction):
        guild = interaction.guild
        await self.config.guild(guild).clear()
        await interaction.response.send_message(
            "Successfully reset all captcha settings to default.", ephemeral=True
        )
