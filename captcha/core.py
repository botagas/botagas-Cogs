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
from .commands import CaptchaCommands
from .format import format_message
from .objects import CaptchaObj

DELETE_AFTER: Final[int] = 10

log: logging.Logger = logging.getLogger("red.seina.captcha")


def captcha_object() -> ModuleType:
    from captcha import objects

    return objects


class Captcha(
    commands.Cog,
    CaptchaCommands,
    metaclass=CompositeMetaClass,
):
    """Captcha cog."""

    __author__: Final[List[str]] = ["inthedark.org"]
    __version__: Final[str] = "0.1.0"

    def __init__(self, bot: Red) -> None:
        super().__init__(bot)
        self._active_challenges: Dict[int, str] = {}
        self.bot: Red = bot
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
        self._dm_messages: Dict[int, discord.Message] = {}

    def register_active_challenge(
        self, user_id: int, code: str, guild_id: int, timeout: int
    ) -> None:
        self._active_challenges[user_id] = {
            "code": code.upper(),
            "guild_id": guild_id,
            "expires_at": asyncio.get_event_loop().time() + timeout,
        }
        asyncio.create_task(self._expire_challenge(user_id, timeout))

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

        try:
            asyncio.create_task(self.cleanup_messages(member.id))
        except Exception as e:
            log.exception(f"Failed to schedule cleanup for {member.id}: {e}")

    async def cleanup_messages(self, member_id: int):
        await asyncio.sleep(DELETE_AFTER)

        for user_try in self._user_tries.get(member_id, []):
            try:
                await user_try.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                log.warning(f"Could not delete message {user_try.id}: {e}")

        try:
            await self._captchas[member_id].delete()
        except (KeyError, discord.HTTPException):
            pass

        self._captchas.pop(member_id, None)
        self._user_tries.pop(member_id, None)

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

        challenge = self._active_challenges.get(message.author.id)
        if not challenge:
            return

        code = challenge["code"]
        guild_id = challenge["guild_id"]
        expires_at = challenge["expires_at"]

        if asyncio.get_event_loop().time() > expires_at:
            await message.channel.send(
                "❌ This captcha session has expired. Please start a new verification."
            )
            self._active_challenges.pop(message.author.id, None)
            return

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

    async def _expire_challenge(self, user_id: int, timeout: int):
        await asyncio.sleep(timeout)
        challenge = self._active_challenges.pop(user_id, None)
        if challenge:
            user = self.bot.get_user(user_id)
            if user:
                try:
                    msg = await user.send(
                        "❌ Time expired for captcha verification. Please try again later."
                    )
                    self._user_tries.setdefault(user_id, []).append(msg)
                except discord.Forbidden:
                    pass
            try:
                asyncio.create_task(self.cleanup_messages(user_id))
            except Exception as e:
                log.exception(f"Failed to schedule cleanup for expired challenge {user_id}: {e}")

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
                msg = await source.followup.send(text, ephemeral=True)
                self._user_tries.setdefault(member.id, []).append(msg)
            else:
                msg = await source.response.send_message(text, ephemeral=True)
                self._user_tries.setdefault(member.id, []).append(msg)
        else:
            await source.channel.send(text)
        asyncio.create_task(self.cleanup_messages(member.id))

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
            msg = await source.channel.send(text)
            self._user_tries.setdefault(member.id, []).append(msg)
        try:
            asyncio.create_task(self.cleanup_messages(member.id))
        except Exception as e:
            log.exception(f"Failed to schedule cleanup for {member.id}: {e}")
        asyncio.create_task(self.cleanup_messages(member.id))
