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

from abc import ABC, ABCMeta, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

class CaptchaVerifyButton(discord.ui.View):
    def __init__(self, cog: "CaptchaCommands", timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="captcha_verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user

        if member.bot:
            return

        guild_config = await self.cog.config.guild(interaction.guild).all()
        channel_id = guild_config["channel"]

        if not channel_id:
            return await interaction.response.send_message("Verification channel not configured.", ephemeral=True)

        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Verification channel is invalid.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        await self.cog.begin_captcha_flow(member, channel)

class MixinMeta(ABC):
    bot: Red
    config: Config
    data_path: Path

    def __init__(self, *_args: Any) -> None:
        super().__init__(*_args)
        self.bot: Red
        self.config: Config

        self._config: Dict[int, Dict[str, Any]]

        self._captchas: Dict[int, discord.Message]
        self._verification_phase: Dict[int, int]
        self._user_tries: Dict[int, List[discord.Message]]

        self.data_path: Path
        self.font_data: str

    @abstractmethod
    def format_help_for_context(self, ctx: commands.Context) -> str:
        raise NotImplementedError()

    @abstractmethod
    async def _initialize(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def _build_cache(self) -> None:
        raise NotImplementedError()


class CompositeMetaClass(commands.CogMeta, ABCMeta):
    pass
