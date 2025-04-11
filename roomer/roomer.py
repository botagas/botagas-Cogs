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

        # Try sending to the linked text channel if available
        if new_channel and new_channel.guild:
            linked_text_channel = discord.utils.get(
                new_channel.guild.text_channels, id=new_channel.id
            )
            if linked_text_channel:
                await linked_text_channel.send(
                    embed=discord.Embed(
                        title="🔧 Voice Channel Controls",
                        description="Use the buttons below to control your channel.",
                        color=discord.Color.blurple(),
                    ),
                    view=ChannelControlView(new_channel),
                )

        # Schedule deletion when empty
        await self.schedule_deletion(new_channel)

    async def schedule_deletion(self, channel):
        await discord.utils.sleep_until(
            discord.utils.utcnow() + discord.utils.timedelta(minutes=1)
        )
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


async def setup(bot):
    await bot.add_cog(Roomer(bot))
