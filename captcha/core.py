"""
MIT License

Copyright (c) 2023-present japandotorg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import logging
import os
import random
import string
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Final, List, Optional, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path

from .abc import CompositeMetaClass
from discord import app_commands
from .format import format_message
from .objects import CaptchaObj

DELETE_AFTER: Final[int] = 10

log: logging.Logger = logging.getLogger("red.seina.captcha")

class Captcha(commands.Cog, metaclass=CompositeMetaClass):
    captcha_group = app_commands.Group(name="captcher", description="Manage Captcha settings.")
    """Captcha cog."""

    __author__: Final[List[str]] = ["inthedark.org"]
    __version__: Final[str] = "0.1.0"

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = Red = bot

        self.bot.tree.add_command(self.__class__.captcha_group)

        self._active_challenges: Dict[int, str] = {}
        self.config: Config = Config.get_conf(
            self,
            identifier=69_420_666,
            force_registration=True,
        )
        default_guild: Dict[str, Union[Optional[int], bool, str]] = {
            "toggle": False,
            "channel": None,
            "timeout": 120,
            "tries": 3,
            "role_before_captcha": None,
            "role_after_captcha": None,
            "message_before_captcha": "{mention}, please solve the captcha below.",
            "message_after_captcha": "✅ {mention}, you passed the captcha!",
            "embed_text": "Click the green button below to verify.",
        }
        self.config.register_guild(**default_guild)

        self._captchas: Dict[int, discord.Message] = {}
        self._verification_phase: Dict[int, int] = {}
        self._user_tries: Dict[int, List[discord.Message]] = {}

        self._config: Dict[int, Dict[str, Any]] = {}

        self.data_path: Path = bundled_data_path(self)
        self.font_data: str = os.path.join(self.data_path, "DroidSansMono.ttf")

        self.task: asyncio.Task = asyncio.create_task(self._initialize())

    def register_active_challenge(self, user_id: int, code: str, guild_id: int) -> None:
        self._active_challenges[user_id] = (code.upper(), guild_id)

    def format_message(self, template: str, member: discord.Member) -> str:
        return template.format(
            mention=member.mention,
            name=member.display_name,
            username=member.name,
            guild=member.guild.name,
            id=member.id,
        )

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre_processed = super().format_help_for_context(ctx) or ""
        n = "\n" if "\n\n" not in pre_processed else ""
        text = [
            f"{pre_processed}{n}",
            f"Cog Version: **{self.__version__}**",
            f"Author: **{self.__author__}**",
        ]
        return "\n".join(text)

    async def _initialize(self) -> None:
        await self.bot.wait_until_red_ready()
        await self._build_cache()

    async def _build_cache(self) -> None:
        self._config: Dict[int, Dict[str, Any]] = await self.config.all_guilds()

    async def cog_unload(self) -> None:
        self.task.cancel()
        await super().cog_unload()

    async def _get_or_fetch_guild(self, guild_id: int) -> Optional[discord.Guild]:
        guild: Optional[discord.Guild] = self.bot.get_guild(guild_id)
        if guild is not None:
            return guild
        if not self.bot.is_ws_ratelimited():
            try:
                guild: Optional[discord.Guild] = await self.bot.fetch_guild(guild_id)
            except discord.HTTPException:
                pass
            else:
                return guild

    async def begin_captcha_flow(self, member: discord.Member, channel: discord.TextChannel):

        self._verification_phase[member.id] = 0
        self._user_tries[member.id] = []

        message_string = "".join(random.choice(string.ascii_uppercase) for _ in range(6))
        captcha = CaptchaObj(self, width=300, height=100)
        captcha.generate(message_string)
        captcha.write(message_string, f"{str(self.data_path)}/{member.id}.png")
        captcha_file = discord.File(f"{str(self.data_path)}/{member.id}.png")

        role_before_id: Optional[int] = await self.config.guild(member.guild).role_before_captcha()
        if role_before_id:
            role_before: Optional[discord.Role] = member.guild.get_role(role_before_id)
            if role_before:
                try:
                    await member.add_roles(role_before, reason="Assigned unverified role on join.")
                except discord.Forbidden:
                    log.warning(f"Could not assign role_before_captcha to {member.id}")

        message_before_captcha = await self.config.guild(member.guild).message_before_captcha()

        color: discord.Color = await self.bot.get_embed_color(channel)

        text = format_message(message_before_captcha, member)
        temp_captcha = await channel.send(content=text, file=captcha_file)

        self._captchas[member.id] = temp_captcha
        self._user_tries[member.id].append(temp_captcha)

        timeout: int = await self.config.guild(member.guild).timeout()

        try:

            def check(message: discord.Message) -> bool:
                return (
                    message.content.upper() == message_string
                    and message.author.id == member.id
                    and message.channel.id == channel.id
                )

            response_message: discord.Message = await self.bot.wait_for(
                "message",
                check=check,
                timeout=timeout,
            )

            try:
                await response_message.delete()
            except discord.HTTPException:
                pass

        except asyncio.TimeoutError:
            await member.kick(
                reason=f"{member.id} failed to solve captcha verification in time.",
            )
            del self._captchas[member.id]
            del self._verification_phase[member.id]
            del self._user_tries[member.id]
        else:
            del self._verification_phase[member.id]

            message_after_captcha = await self.config.guild(member.guild).message_after_captcha()

            color: discord.Color = await self.bot.get_embed_color(channel)

            text = self.format_message(message_after_captcha, member)
            temp_success_message = await channel.send(content=text)

            self._user_tries[member.id].append(temp_success_message)

            os.remove(f"{str(self.data_path)}/{member.id}.png")

            role_before_id: Optional[int] = await self.config.guild(
                member.guild
            ).role_before_captcha()
            if role_before_id:
                role_before: Optional[discord.Role] = member.guild.get_role(role_before_id)
                if role_before:
                    try:
                        await member.remove_roles(
                            role_before, reason="Captcha passed, removing unverified role."
                        )
                    except discord.Forbidden:
                        log.warning(f"Could not remove role_before_captcha from {member.id}")

            role_id: int = await self.config.guild(member.guild).role_after_captcha()
            role: Optional[discord.Role] = discord.utils.get(member.guild.roles, id=role_id)

            try:
                await member.add_roles(role, reason=f"Captcha solved by {member.display_name}!")  # type: ignore
            except (discord.Forbidden, discord.HTTPException):
                log.exception(f"Failed to add roles to {member.id}.", exc_info=True)

        async def cleanup_messages():
            await asyncio.sleep(DELETE_AFTER)

            for user_try in self._user_tries.get(member.id, []):
                try:
                    await user_try.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    log.warning(f"Could not delete message {user_try.id}: {e}")

            try:
                await self._captchas[member.id].delete()
            except (KeyError, discord.HTTPException):
                pass

            self._captchas.pop(member.id, None)
            self._user_tries.pop(member.id, None)

        asyncio.create_task(cleanup_messages())

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        member: Union[discord.Member, discord.User] = payload.user
        guild: Optional[discord.Guild] = await self._get_or_fetch_guild(payload.guild_id)
        if guild is None:
            return
        if await self.bot.cog_disabled_in_guild_raw(self.__class__.__name__, payload.guild_id):
            return
        if not await self.config.guild(guild).toggle():
            return

        if (
            not guild.me.guild_permissions.kick_members
            or not guild.me.guild_permissions.manage_roles
            or not guild.me.guild_permissions.embed_links
            or not guild.me.guild_permissions.attach_files
        ):
            await self.config.guild(guild).toggle.set(False)
            log.info("Disabled captcha verification due to missing permissions.")
            return

        if member.id in self._verification_phase:
            for user_try in self._user_tries[member.id]:
                await user_try.delete()
            await self._captchas[member.id].delete()
            del self._captchas[member.id]
            try:
                del self._verification_phase[member.id]
            except KeyError:
                pass
            os.remove(f"{str(self.data_path)}/{member.id}.png")
            del self._user_tries[member.id]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None or message.author.bot:
            return

        code_info = self._active_challenges.get(message.author.id)
        if not code_info:
            return  # No active challenge

        code, guild_id = code_info
        if message.content.strip().upper() == code:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            member = guild.get_member(message.author.id)
            if not member:
                try:
                    member = await guild.fetch_member(message.author.id)
                except discord.HTTPException:
                    return
            await self._on_captcha_success(member, message)
        else:
            await self._on_captcha_failure(message.author, message)

        self.cleanup_captcha_image(message.author.id)
        self._active_challenges.pop(message.author.id, None)

    def generate_captcha_code(self) -> str:
        return "".join(random.choice(string.ascii_uppercase) for _ in range(6))

    def save_captcha_image(self, code: str, user_id: int) -> str:
        path = os.path.join(str(self.data_path), f"{user_id}.png")
        captcha = CaptchaObj(self, width=300, height=100)
        captcha.write(code, path)
        return path

    def cleanup_captcha_image(self, user_id: int):
        path = os.path.join(str(self.data_path), f"{user_id}.png")
        if os.path.exists(path):
            os.remove(path)

    async def _on_captcha_failure(
        self, member: discord.abc.User, source: discord.Interaction | discord.Message
    ):
        text = "❌ Incorrect captcha. Please try again or contact an admin."
        if isinstance(source, discord.Interaction):
            if source.response.is_done():
                await source.followup.send(text, ephemeral=True)
            else:
                await source.response.send_message(text, ephemeral=True)
        else:
            await source.channel.send(text)

    async def _on_captcha_success(
        self, member: discord.Member, source: discord.Interaction | discord.Message
    ):
        # Remove unverified role
        unverified_id = await self.config.guild(member.guild).role_before_captcha()
        unverified = member.guild.get_role(unverified_id) if unverified_id else None
        if unverified:
            try:
                await member.remove_roles(
                    unverified, reason="Captcha passed - removing unverified role"
                )
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

        message_after = await self.config.guild(member.guild).message_after_captcha()
        text = self.format_message(message_after, member)

        if isinstance(source, discord.Interaction):
            if source.response.is_done():
                await source.followup.send(text, ephemeral=True)
            else:
                await source.response.send_message(text, ephemeral=True)
        else:
            await source.channel.send(text)

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
        await channel.send(embed=embed, view=view)
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

    @captcha_group.command(
        name="embed", description="Set the text shown in the verification embed."
    )
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
        if captcha_info:
            try:
                channel = guild.get_channel(captcha_info["channel_id"])
                if channel:
                    msg = await channel.fetch_message(captcha_info["message_id"])
                    embed = discord.Embed(
                        description=format_message(message, guild.me),
                        color=discord.Color(0x34EB83),
                    )
                    await msg.edit(embed=embed)
            except Exception as e:
                await interaction.followup.send(
                    f"Embed updated, but I couldn't update the deployed message: `{e}`",
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
