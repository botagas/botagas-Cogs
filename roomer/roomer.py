from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands
from redbot.core import Config
from redbot.core import commands as red_commands
from redbot.core.i18n import Translator, cog_i18n

roomer_group = app_commands.Group(name="roomer", description="Roomer configuration.")

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
        self.channel_owners = {}

    async def red_delete_data_for_user(self, **kwargs):
        return

    @roomer_group.command(name="enable", description="Enable automatic voice channel creation.")
    @app_commands.checks.has_permissions(administrator=True)
    async def enable(self, interaction: discord.Interaction):
        """Enable automatic voice channel creation."""
        await self.config.guild(interaction.guild).auto_enabled.set(True)
        await interaction.response.send_message("Automatic voicechannel creation enabled.")

    @roomer_group.command(name="disable", description="Disable automatic voice channel creation.")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        """Disable automatic voice channel creation."""
        await self.config.guild(interaction.guild).auto_enabled.set(False)
        await interaction.response.send_message("Automatic voicechannel creation disabled.")

    @roomer_group.command(name="add", description="Add a join-to-create channel.")
    @app_commands.describe(channel="Voice channel to designate as join-to-create")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_channel(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        """Add a join-to-create channel."""
        channels = await self.config.guild(interaction.guild).auto_channels()
        if channel.id not in channels:
            channels.append(channel.id)
            await self.config.guild(interaction.guild).auto_channels.set(channels)
            await interaction.response.send_message(
                f"Added {channel.mention} as a join-to-create channel."
            )
        else:
            await interaction.response.send_message("That channel is already configured.")

    @roomer_group.command(name="remove", description="Remove a join-to-create channel.")
    @app_commands.describe(channel="Voice channel to remove from join-to-create")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_channel(
        self, interaction: discord.Interaction, channel: discord.VoiceChannel
    ):
        """Remove a join-to-create channel."""
        channels = await self.config.guild(interaction.guild).auto_channels()
        if channel.id in channels:
            channels.remove(channel.id)
            await self.config.guild(interaction.guild).auto_channels.set(channels)
            await interaction.response.send_message(
                f"Removed {channel.mention} from join-to-create channels."
            )
        else:
            await interaction.response.send_message("That channel wasn't configured.")

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
            user_limit=min(settings["user_limit"] or 0, 99),
            reason="Auto voice channel creation",
        )
        await member.move_to(new_channel, reason="Moved to new voice room")

        self.channel_owners[new_channel.id] = member.id

        try:
            view = ChannelControlView(new_channel, member.id, self)
            await new_channel.send(
                embed=discord.Embed(
                    title="🔧 Voice Channel Controls",
                    description="Use the buttons below to control your channel.",
                    color=discord.Color.blurple(),
                ),
                view=view,
            )
        except Exception:
            pass
        await self.schedule_deletion(new_channel)

    async def schedule_deletion(self, channel):
        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(minutes=1))
        if len(channel.members) == 0:
            try:
                await channel.delete(reason="Temporary voice channel expired")
            except discord.NotFound:
                pass


class SetStatusModal(discord.ui.Modal, title="Set Channel Status"):
    status = discord.ui.TextInput(
        label="Channel Status (shown below name)",
        placeholder="e.g. Chilling, Gaming",
        max_length=100,
    )

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.edit(topic=self.status.value)
        await interaction.response.send_message(
            f"✅ Channel status updated to **{self.status.value}**.", ephemeral=True
        )


class ForbidSelect(discord.ui.Select):
    def __init__(self, channel: discord.VoiceChannel):
        self.channel = channel
        options = []

        # Add members who do not have connect=False
        for member in channel.members:
            perms = channel.overwrites_for(member)
            if perms.connect is not False:
                options.append(
                    discord.SelectOption(label=member.display_name, value=f"user:{member.id}")
                )

        # Add roles (excluding @everyone, managed roles, and already forbidden)
        for role in channel.guild.roles:
            if role.is_default() or role.managed:
                continue
            perms = channel.overwrites_for(role)
            if perms.connect is not False:
                options.append(
                    discord.SelectOption(label=f"@{role.name}", value=f"role:{role.id}")
                )

        super().__init__(
            placeholder="Select a user or role to forbid",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        kind, identifier = self.values[0].split(":")
        target = None
        if kind == "user":
            target = self.channel.guild.get_member(int(identifier))
        else:
            target = self.channel.guild.get_role(int(identifier))

        if target:
            await self.channel.set_permissions(target, connect=False)
            await interaction.response.send_message(
                f"❌ {target.mention} has been forbidden from joining this channel.",
                ephemeral=True,
            )


class PermitSelect(discord.ui.Select):
    def __init__(self, channel: discord.VoiceChannel):
        self.channel = channel
        options = []

        # Add members who do not have connect=True
        for member in channel.members:
            perms = channel.overwrites_for(member)
            if perms.connect is not True:
                options.append(
                    discord.SelectOption(label=member.display_name, value=f"user:{member.id}")
                )

        # Add roles (excluding @everyone, managed roles, and already permitted)
        for role in channel.guild.roles:
            if role.is_default() or role.managed:
                continue
            perms = channel.overwrites_for(role)
            if perms.connect is not True:
                options.append(
                    discord.SelectOption(label=f"@{role.name}", value=f"role:{role.id}")
                )

        super().__init__(
            placeholder="Select a user or role to permit",
            min_values=1,
            max_values=1,
            options=options[:25],  # Discord max
        )

    async def callback(self, interaction: discord.Interaction):
        kind, identifier = self.values[0].split(":")
        target = None
        if kind == "user":
            target = self.channel.guild.get_member(int(identifier))
        else:
            target = self.channel.guild.get_role(int(identifier))

        if target:
            await self.channel.set_permissions(target, connect=True)
            await interaction.response.send_message(
                f"✅ Permitted {target.mention} to join this channel.", ephemeral=True
            )


class ChannelControlView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int, cog: Roomer):
        super().__init__(timeout=None)
        self.channel = channel
        self.owner_id = owner_id
        self.cog = cog

    async def _check_permissions(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ You are not the owner of this voice channel.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="🔓 Lock", style=discord.ButtonStyle.danger)
    async def toggle_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        overwrites = self.channel.overwrites
        current = overwrites.get(self.channel.guild.default_role, discord.PermissionOverwrite())
        currently_locked = current.connect is False

        new_overwrite = discord.PermissionOverwrite(
            view_channel=current.view_channel, connect=None if currently_locked else False
        )
        overwrites[self.channel.guild.default_role] = new_overwrite
        await self.channel.edit(overwrites=overwrites)

        updated = self.channel.overwrites.get(
            self.channel.guild.default_role, discord.PermissionOverwrite()
        )
        locked = updated.connect is False

        # Update button labels and styles
        button.label = "🔓 Unlock" if locked else "🔒 Lock"
        button.style = discord.ButtonStyle.success if locked else discord.ButtonStyle.danger

        # Update the message with the updated view
        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            "🔓 Channel unlocked." if currently_locked else "🔒 Channel locked.", ephemeral=True
        )

    @discord.ui.button(label="👁 Hide", style=discord.ButtonStyle.secondary)
    async def toggle_visibility(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        overwrites = self.channel.overwrites
        current = overwrites.get(self.channel.guild.default_role, discord.PermissionOverwrite())
        currently_hidden = current.view_channel is False

        new_overwrite = discord.PermissionOverwrite(
            connect=current.connect, view_channel=None if currently_hidden else False
        )
        overwrites[self.channel.guild.default_role] = new_overwrite
        await self.channel.edit(overwrites=overwrites)

        updated = self.channel.overwrites.get(
            self.channel.guild.default_role, discord.PermissionOverwrite()
        )
        hidden = updated.connect is False

        # Update button labels and styles
        button.style = discord.ButtonStyle.success if hidden else discord.ButtonStyle.danger

        # Update the message with the updated view
        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            (
                "👁 Channel is now visible to everyone."
                if currently_hidden
                else "🙈 Channel hidden from others."
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="➕ Permit", style=discord.ButtonStyle.success)
    async def permit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        select = PermitSelect(self.channel)
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message(
            "Select a user or role to permit:", view=view, ephemeral=True
        )

    @discord.ui.button(label="➖ Forbid", style=discord.ButtonStyle.danger)
    async def forbid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        select = ForbidSelect(self.channel)
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message(
            "Select a user or role to forbid:", view=view, ephemeral=True
        )

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.primary)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        modal = RenameModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📝 Set Status", style=discord.ButtonStyle.secondary)
    async def set_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        modal = SetStatusModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="👥 Set Limit", style=discord.ButtonStyle.secondary)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        modal = LimitModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔄 Reset Channel", style=discord.ButtonStyle.secondary)
    async def reset_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        overwrites = self.channel.overwrites
        if self.channel.guild.default_role in overwrites:
            del overwrites[self.channel.guild.default_role]
        for target in list(overwrites):
            if target != self.channel.guild.default_role:
                del overwrites[target]

        await self.channel.edit(name="Voice Room", user_limit=0, topic=None, overwrites=overwrites)
        await interaction.response.send_message(
            "🔄 Channel reset to default settings.", ephemeral=True
        )

    @discord.ui.button(label="🎙 Claim Room", style=discord.ButtonStyle.secondary)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            cog = self.cog
            current_owner_id = cog.channel_owners.get(self.channel.id)
            if current_owner_id == interaction.user.id:
                await interaction.response.send_message(
                    "✅ You already own this room.", ephemeral=True
                )
                return

            current_owner = self.channel.guild.get_member(current_owner_id)
            if not current_owner or current_owner not in self.channel.members:
                self.owner_id = interaction.user.id
                cog.channel_owners[self.channel.id] = interaction.user.id
                await interaction.response.send_message(
                    "✅ You have claimed ownership of this room.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ The current owner is still in the room.", ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(f"❌ Claim failed: {e}", ephemeral=True)


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
            value = min(value, 99)
            await self.channel.edit(user_limit=value)
            await interaction.response.send_message(
                f"✅ User limit set to **{value or 'unlimited'}**.", ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message("❌ Invalid input.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Roomer(bot))
