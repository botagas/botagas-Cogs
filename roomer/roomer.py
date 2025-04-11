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
    Sends an interactive menu to the voice channel's thread.
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

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not after or not after.channel:
            return

        guild = member.guild
        settings = await self.config.guild(guild).all()

        if not settings["auto_enabled"]:
            return

        if after.channel.id not in settings["auto_channels"]:
            return

        # Create the temporary voice channel
        new_channel = await after.channel.category.create_voice_channel(
            name=settings["name"],
            user_limit=settings["user_limit"],
            overwrites=after.channel.overwrites,
            reason="Auto voice channel creation via Roomer.",
        )
        await member.move_to(new_channel, reason="Joined auto-created channel")

        # Create thread and send menu
        thread = await new_channel.create_text_channel(
            name=f"{member.display_name}'s Room Controls",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            },
            reason="Roomer: text control thread",
        )

        view = RoomSettingsView(channel=new_channel)
        await thread.send(
            content="Welcome! Use the menu below to manage your voice room:", view=view
        )


class RoomSettingsView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=None)
        self.channel = channel

    @discord.ui.button(label="Lock Room", style=discord.ButtonStyle.danger, custom_id="lock_room")
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        overwrites = self.channel.overwrites
        overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(connect=False)
        await self.channel.edit(overwrites=overwrites)
        await interaction.response.send_message("Room locked.", ephemeral=True)

    @discord.ui.button(
        label="Unlock Room", style=discord.ButtonStyle.success, custom_id="unlock_room"
    )
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        overwrites = self.channel.overwrites
        overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(connect=True)
        await self.channel.edit(overwrites=overwrites)
        await interaction.response.send_message("Room unlocked.", ephemeral=True)

    @discord.ui.button(
        label="Rename Room", style=discord.ButtonStyle.primary, custom_id="rename_room"
    )
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RenameRoomModal(self.channel)
        await interaction.response.send_modal(modal)


class RenameRoomModal(discord.ui.Modal, title="Rename Voice Room"):
    new_name = discord.ui.TextInput(label="New name", max_length=100)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.edit(name=self.new_name.value)
        await interaction.response.send_message(
            f"Room renamed to **{self.new_name.value}**.", ephemeral=True
        )
