import discord
from discord import app_commands
from discord.ext import commands
from redbot.core import Config
from redbot.core import commands as red_commands
from redbot.core.i18n import Translator, cog_i18n

_ = Translator("Roomer", __file__)


@cog_i18n(_)
class Roomer(red_commands.Cog):
    """
    Automatically create temporary voice channels when users join a join-to-create channel.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=300620201743, force_registration=True)
        self.config.register_guild(
            auto_channels=[],
            auto_enabled=False,
            name="Voice Room",
            user_limit=None,
        )

    async def red_delete_data_for_user(self, **kwargs):
        return

    @red_commands.hybrid_group(name="roomer", with_app_command=True)
    @red_commands.guild_only()
    async def roomer(self, ctx: red_commands.Context):
        """Roomer configuration."""

    @roomer.command(name="enable")
    async def enable(self, ctx: red_commands.Context):
        """Enable automatic voice channel creation."""
        await self.config.guild(ctx.guild).auto_enabled.set(True)
        await ctx.send("Automatic voicechannel creation enabled.")

    @roomer.command(name="disable")
    async def disable(self, ctx: red_commands.Context):
        """Disable automatic voice channel creation."""
        await self.config.guild(ctx.guild).auto_enabled.set(False)
        await ctx.send("Automatic voicechannel creation disabled.")

    @roomer.command(name="add")
    async def add_channel(self, ctx: red_commands.Context, channel: discord.VoiceChannel):
        """Add a join-to-create channel."""
        channels = await self.config.guild(ctx.guild).auto_channels()
        if channel.id not in channels:
            channels.append(channel.id)
            await self.config.guild(ctx.guild).auto_channels.set(channels)
            await ctx.send(f"Added {channel.mention} as a join-to-create channel.")
        else:
            await ctx.send("That channel is already configured.")

    @roomer.command(name="remove")
    async def remove_channel(self, ctx: red_commands.Context, channel: discord.VoiceChannel):
        """Remove a join-to-create channel."""
        channels = await self.config.guild(ctx.guild).auto_channels()
        if channel.id in channels:
            channels.remove(channel.id)
            await self.config.guild(ctx.guild).auto_channels.set(channels)
            await ctx.send(f"Removed {channel.mention} from join-to-create channels.")
        else:
            await ctx.send("That channel wasn't configured.")

    @roomer.command(name="name")
    async def set_name(self, ctx: red_commands.Context, *, name: str):
        """Set the name for auto-created voice channels."""
        await self.config.guild(ctx.guild).name.set(name)
        await ctx.send(f"Voice channels will now be named: **{name}**")

    @roomer.command(name="limit")
    async def set_limit(self, ctx: red_commands.Context, limit: int = 0):
        """Set user limit for auto-created voice channels (0 = no limit)."""
        await self.config.guild(ctx.guild).user_limit.set(limit)
        await ctx.send(f"User limit set to {limit}.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member.guild:
            return

        settings = await self.config.guild(member.guild).all()
        if not settings["auto_enabled"]:
            return

        if after.channel and after.channel.id in settings["auto_channels"]:
            # Create a new voice channel in same category
            category = after.channel.category
            overwrites = after.channel.overwrites
            new_channel = await category.create_voice_channel(
                settings["name"],
                overwrites=overwrites,
                user_limit=settings["user_limit"] or 0,
                reason="Roomer: Auto VC creation.",
            )
            await member.move_to(new_channel)

        if before.channel and before.channel != after.channel:
            if (
                before.channel.id not in settings["auto_channels"]
                and len(before.channel.members) == 0
            ):
                if before.channel.name == settings["name"]:
                    try:
                        await before.channel.delete(reason="Roomer: Auto VC cleanup.")
                    except discord.Forbidden:
                        pass
