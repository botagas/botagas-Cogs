from datetime import timedelta

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
    @red_commands.has_permissions(administrator=True)
    async def roomer(self, ctx: red_commands.Context):
        """Roomer configuration."""

    @roomer.command(name="enable")
    @red_commands.has_permissions(administrator=True)
    async def enable(self, ctx: red_commands.Context):
        """Enable automatic voice channel creation."""
        await self.config.guild(ctx.guild).auto_enabled.set(True)
        await ctx.send("Automatic voicechannel creation enabled.")

    @roomer.command(name="disable")
    @red_commands.has_permissions(administrator=True)
    async def disable(self, ctx: red_commands.Context):
        """Disable automatic voice channel creation."""
        await self.config.guild(ctx.guild).auto_enabled.set(False)
        await ctx.send("Automatic voicechannel creation disabled.")

    @roomer.command(name="add")
    @red_commands.has_permissions(administrator=True)
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
    @red_commands.has_permissions(administrator=True)
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
    @red_commands.has_permissions(administrator=True)
    async def set_name(self, ctx: red_commands.Context, *, name: str):
        """Set the name for auto-created voice channels."""
        await self.config.guild(ctx.guild).name.set(name)
        await ctx.send(f"Voice channels will now be named: **{name}**")

    @roomer.command(name="limit")
    @red_commands.has_permissions(administrator=True)
    async def set_limit(self, ctx: red_commands.Context, limit: int = 0):
        """Set user limit for auto-created voice channels (0 = no limit)."""
        await self.config.guild(ctx.guild).user_limit.set(limit)
        await ctx.send(f"User limit set to {limit}.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not after or not after.channel:
            return

        guild = member.guild
        settings = await self.config.guild(guild).all()

        if not settings["auto_enabled"] or after.channel.id not in settings["auto_channels"]:
            return

        category = after.channel.category
        new_channel = await category.create_voice_channel(
            settings["name"],
            overwrites=after.channel.overwrites,
            user_limit=settings["user_limit"] or 0,
            reason="Auto voice channel creation",
        )
        await member.move_to(new_channel, reason="Moved to new voice room")

        # Send embed to the voice channel's associated text chat
        try:
            await new_channel.send(
                embed=discord.Embed(
                    title="🔧 Voice Channel Controls",
                    description="Use the buttons below to control your channel.",
                    color=discord.Color.blurple(),
                ),
                view=ChannelControlView(new_channel),
            )
        except Exception:
            pass

        await self.schedule_deletion(new_channel)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, red_commands.MissingPermissions):
            await ctx.send("🚫 You do not have permission to use this command.")
        else:
            raise error  # Re-raise unhandled errors so Redbot can deal with them

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("🚫 You do not have permission to use this command.", ephemeral=True)
        else:
            raise error

    async def schedule_deletion(self, channel):
        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(minutes=1))
        if len(channel.members) == 0:
            await channel.delete(reason="Temporary voice channel expired")


class ChannelControlView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__(timeout=None)
        self.channel = channel

    @discord.ui.button(label="🔒 Lock", style=discord.ButtonStyle.danger)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        overwrites = self.channel.overwrites
        overwrites[self.channel.guild.default_role] = discord.PermissionOverwrite(connect=False)
        await self.channel.edit(overwrites=overwrites)
        await interaction.response.send_message("🔒 Channel locked.", ephemeral=True)

    @discord.ui.button(label="🔓 Unlock", style=discord.ButtonStyle.success)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        overwrites = self.channel.overwrites
        if self.channel.guild.default_role in overwrites:
            del overwrites[self.channel.guild.default_role]
        await self.channel.edit(overwrites=overwrites)
        await interaction.response.send_message("🔓 Channel unlocked.", ephemeral=True)

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.primary)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RenameModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="👥 Set Limit", style=discord.ButtonStyle.secondary)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = LimitModal(self.channel)
        await interaction.response.send_modal(modal)


class RenameModal(discord.ui.Modal, title="Rename Voice Channel"):
    name = discord.ui.TextInput(
        label="New Channel Name", placeholder="Enter name...", max_length=100
    )

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.edit(name=self.name.value)
        await interaction.response.send_message(
            f"✅ Renamed channel to **{self.name.value}**.", ephemeral=True
        )


class LimitModal(discord.ui.Modal, title="Set Channel User Limit"):
    limit = discord.ui.TextInput(
        label="User Limit (leave blank for unlimited)",
        placeholder="e.g. 5",
        required=False,
        max_length=3,
    )

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.limit.value) if self.limit.value else 0
            await self.channel.edit(user_limit=value)
            await interaction.response.send_message(
                f"✅ User limit set to **{value or 'unlimited'}**.", ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message("❌ Invalid input.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Roomer(bot))
