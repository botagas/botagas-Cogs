import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from redbot.core import Config
from redbot.core import commands as red_commands
from redbot.core.i18n import Translator, cog_i18n

_ = Translator("Roomer", __file__)
log = logging.getLogger("red.botagas.roomer")


@cog_i18n(_)
class Roomer(red_commands.Cog):
    """
    Automatically create temporary voice channels when users join a join-to-create channel.
    """

    roomer_group = app_commands.Group(name="roomer", description="Roomer configuration.")

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=300620201743, force_registration=True)
        self.config.register_guild(
            auto_channels=[],
            auto_enabled=False,
            name="Voice Room",
            user_limit=None,
            presets={},
            rooms={},
        )
        self.channel_owners = {}
        self.reminder_messages = {}
        self._restore_task = asyncio.create_task(self._restore_rooms())

    async def cog_unload(self):
        self._restore_task.cancel()

    async def _restore_rooms(self):
        await self.bot.wait_until_red_ready()
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            presets = data.get("presets", {})
            presets_changed = False
            for preset in presets.values():
                defaults = {
                    "game_name": "",
                    "game_aliases": [],
                    "announcement_role_id": None,
                    "announcement_description": "",
                    "announcement_image_url": "",
                }
                for key, value in defaults.items():
                    if key not in preset:
                        preset[key] = value
                        presets_changed = True
            if presets_changed:
                await self.config.guild(guild).presets.set(presets)
            rooms = data.get("rooms", {})
            changed = False
            for channel_id, room in list(rooms.items()):
                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.VoiceChannel):
                    rooms.pop(channel_id, None)
                    changed = True
                    continue
                owner_id = room.get("owner_id")
                if not owner_id:
                    rooms.pop(channel_id, None)
                    changed = True
                    continue
                self.channel_owners[channel.id] = owner_id
                message_id = room.get("control_message_id")
                if message_id:
                    self.bot.add_view(
                        ChannelControlView(channel, owner_id, self), message_id=message_id
                    )
            if changed:
                await self.config.guild(guild).rooms.set(rooms)

    async def get_room(self, channel_id: int) -> Optional[dict]:
        for guild in self.bot.guilds:
            room = (await self.config.guild(guild).rooms()).get(str(channel_id))
            if room:
                return room
        return None

    def get_room_owner_id(self, channel_id: int) -> Optional[int]:
        return self.channel_owners.get(channel_id)

    def is_room_owner(self, channel: discord.VoiceChannel, member: discord.Member) -> bool:
        return self.channel_owners.get(channel.id) == member.id and member in channel.members

    async def _save_room(
        self,
        channel: discord.VoiceChannel,
        owner_id: int,
        control_message_id: Optional[int] = None,
        selected_preset: Optional[str] = None,
    ) -> None:
        async with self.config.guild(channel.guild).rooms() as rooms:
            current = rooms.get(str(channel.id), {})
            current.update(
                {
                    "owner_id": owner_id,
                    "control_message_id": (
                        control_message_id
                        if control_message_id is not None
                        else current.get("control_message_id")
                    ),
                    "selected_preset": selected_preset,
                }
            )
            rooms[str(channel.id)] = current

    async def set_room_owner(self, channel: discord.VoiceChannel, owner_id: int) -> None:
        self.channel_owners[channel.id] = owner_id
        room = await self.get_room(channel.id) or {}
        await self._save_room(
            channel,
            owner_id,
            room.get("control_message_id"),
            room.get("selected_preset"),
        )
        self.bot.dispatch("roomer_owner_changed", channel, owner_id)

    async def set_room_preset(
        self, channel: discord.VoiceChannel, preset_name: Optional[str]
    ) -> None:
        room = await self.get_room(channel.id) or {}
        await self._save_room(
            channel,
            room.get("owner_id", self.channel_owners.get(channel.id)),
            room.get("control_message_id"),
            preset_name,
        )
        presets = await self.config.guild(channel.guild).presets()
        preset = presets.get(preset_name, {})
        announcer = self.bot.get_cog("RoomAnnounce")
        if announcer is not None:
            await announcer.sync_room_preset(channel, preset_name, preset)
        self.bot.dispatch("roomer_preset_applied", channel, preset_name, preset)

    async def apply_room_preset(self, channel: discord.VoiceChannel, preset_name: str) -> dict:
        presets = await self.config.guild(channel.guild).presets()
        preset = presets.get(preset_name)
        if preset is None:
            raise ValueError(f"Preset `{preset_name}` does not exist.")
        await channel.edit(
            name=preset["title"],
            status=preset.get("status") or None,
            user_limit=min(preset.get("limit") or 0, 99),
        )
        await self.set_room_preset(channel, preset_name)
        return preset

    async def red_delete_data_for_user(self, **kwargs):
        user_id = kwargs.get("user_id")
        if not user_id:
            return
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            rooms = data.get("rooms", {})
            changed = False
            for channel_id, room in rooms.items():
                if room.get("owner_id") != user_id:
                    continue
                room["owner_id"] = guild.me.id
                self.channel_owners[int(channel_id)] = guild.me.id
                channel = guild.get_channel(int(channel_id))
                if isinstance(channel, discord.VoiceChannel):
                    member = guild.get_member(user_id)
                    if member:
                        try:
                            await channel.set_permissions(member, overwrite=None)
                        except (discord.Forbidden, discord.HTTPException):
                            log.warning(
                                "Could not remove stored room permissions for user %s",
                                user_id,
                            )
                    self.bot.dispatch("roomer_owner_changed", channel, guild.me.id)
                changed = True
            if changed:
                await self.config.guild(guild).rooms.set(rooms)

    async def send_claim_reminder(self, channel: discord.VoiceChannel):
        """Send a reminder to users in the channel to claim ownership."""
        if len(channel.members) > 0:
            try:
                message = await channel.send(
                    "⚠️ The owner has left the room. You can claim ownership by clicking the '🎙 Claim Room' button."
                )
                self.reminder_messages[channel.id] = message
            except discord.Forbidden:
                self.bot.logger.warning(f"Failed to send reminder in {channel.name}.")

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

    @roomer_group.command(name="preset", description="Manage voice channel presets.")
    @app_commands.describe(
        action="add, edit or delete",
        name="Name of the preset",
        title="Optional title for the voice channel",
        status="Optional status for the channel",
        limit="Optional user limit (0-99)",
        game="Game represented by this preset",
        aliases="Comma-separated game aliases used for detection",
        announce_role="Role tagged by room announcements",
        announce_description="Default announcement description",
        announce_image="Default HTTPS announcement image",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="edit", value="edit"),
            app_commands.Choice(name="delete", value="delete"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def preset(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        name: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        game: Optional[str] = None,
        aliases: Optional[str] = None,
        announce_role: Optional[discord.Role] = None,
        announce_description: Optional[str] = None,
        announce_image: Optional[str] = None,
    ):
        presets = await self.config.guild(interaction.guild).presets()

        for value, maximum, label in (
            (game, 100, "Game"),
            (aliases, 1000, "Aliases"),
            (announce_description, 1000, "Announcement description"),
            (announce_image, 1000, "Announcement image URL"),
        ):
            if value and len(value) > maximum:
                return await interaction.response.send_message(
                    f"❌ {label} must be {maximum} characters or less.", ephemeral=True
                )

        if action.value == "add":
            if not name or not title:
                return await interaction.response.send_message(
                    "❌ Provide both name and title to add a preset.", ephemeral=True
                )
            if name in presets:
                await interaction.response.send_message(
                    f"❌ Preset `{name}` already exists.", ephemeral=True
                )
                return

            if len(title) > 100:
                return await interaction.response.send_message(
                    "❌ Title must be 100 characters or less.", ephemeral=True
                )

            if status and len(status) > 500:
                return await interaction.response.send_message(
                    "❌ Status must be 500 characters or less.", ephemeral=True
                )

            if limit and (limit < 0 or limit > 99):
                return await interaction.response.send_message(
                    "❌ Limit must be between 0 and 99.", ephemeral=True
                )

            if announce_image and not announce_image.startswith("https://"):
                return await interaction.response.send_message(
                    "❌ Announcement images must use an HTTPS URL.", ephemeral=True
                )
            presets[name] = {
                "title": title,
                "status": status or "",
                "limit": limit,
                "game_name": game or "",
                "game_aliases": [
                    item.strip() for item in (aliases or "").split(",") if item.strip()
                ],
                "announcement_role_id": announce_role.id if announce_role else None,
                "announcement_description": announce_description or "",
                "announcement_image_url": announce_image or "",
            }
            await self.config.guild(interaction.guild).presets.set(presets)
            await interaction.response.send_message(
                f"✅ Preset `{name}` has been added.", ephemeral=True
            )
        elif action.value == "edit":
            if name not in presets:
                await interaction.response.send_message(
                    f"❌ Preset `{name}` does not exist.", ephemeral=True
                )
                return

            # Update only the fields that are provided
            if title:
                if len(title) > 100:
                    return await interaction.response.send_message(
                        "❌ Title must be 100 characters or less.", ephemeral=True
                    )
                presets[name]["title"] = title

            if status:
                if len(status) > 500:
                    return await interaction.response.send_message(
                        "❌ Status must be 500 characters or less.", ephemeral=True
                    )
                presets[name]["status"] = status

            if limit is not None:
                if limit < 0 or limit > 99:
                    return await interaction.response.send_message(
                        "❌ Limit must be between 0 and 99.", ephemeral=True
                    )
                presets[name]["limit"] = limit

            if game is not None:
                presets[name]["game_name"] = game
            if aliases is not None:
                presets[name]["game_aliases"] = [
                    item.strip() for item in aliases.split(",") if item.strip()
                ]
            if announce_role is not None:
                presets[name]["announcement_role_id"] = announce_role.id
            if announce_description is not None:
                presets[name]["announcement_description"] = announce_description
            if announce_image is not None:
                if announce_image and not announce_image.startswith("https://"):
                    return await interaction.response.send_message(
                        "❌ Announcement images must use an HTTPS URL.", ephemeral=True
                    )
                presets[name]["announcement_image_url"] = announce_image

            await self.config.guild(interaction.guild).presets.set(presets)
            await interaction.response.send_message(
                f"✅ Preset `{name}` has been updated.", ephemeral=True
            )

        elif action.value == "delete":
            if not name or name not in presets:
                return await interaction.response.send_message(
                    f"❌ Preset `{name}` does not exist.", ephemeral=True
                )

            del presets[name]
            await self.config.guild(interaction.guild).presets.set(presets)
            await interaction.response.send_message(
                f"🗑️ Preset `{name}` has been deleted.", ephemeral=True
            )

        elif action.value == "list":
            if not presets:
                await interaction.response.send_message("No presets defined.", ephemeral=True)
                return

            embed = discord.Embed(title="🎮 Available Presets", color=discord.Color.blurple())
            for name, data in presets.items():
                desc = f"**Title:** {data.get('title') or 'N/A'}\n"
                desc += f"**Status:** {data.get('status') or 'None'}\n"
                desc += (
                    f"**Limit:** {data.get('limit') if data.get('limit') is not None else 'None'}"
                )
                desc += f"\n**Game:** {data.get('game_name') or 'None'}"
                role_id = data.get("announcement_role_id")
                desc += f"\n**Announcement role:** {f'<@&{role_id}>' if role_id else 'None'}"
                embed.add_field(name=name, value=desc, inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @roomer_group.command(name="presetclear", description="Clear a preset announcement field.")
    @app_commands.describe(name="Preset name", field="Announcement field to clear")
    @app_commands.choices(
        field=[
            app_commands.Choice(name="game", value="game_name"),
            app_commands.Choice(name="aliases", value="game_aliases"),
            app_commands.Choice(name="role", value="announcement_role_id"),
            app_commands.Choice(name="description", value="announcement_description"),
            app_commands.Choice(name="image", value="announcement_image_url"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def presetclear(
        self,
        interaction: discord.Interaction,
        name: str,
        field: app_commands.Choice[str],
    ):
        presets = await self.config.guild(interaction.guild).presets()
        if name not in presets:
            return await interaction.response.send_message(
                f"❌ Preset `{name}` does not exist.", ephemeral=True
            )
        presets[name][field.value] = [] if field.value == "game_aliases" else None
        await self.config.guild(interaction.guild).presets.set(presets)
        await interaction.response.send_message(
            f"✅ Cleared `{field.name}` from preset `{name}`.", ephemeral=True
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if (
            before.channel
            and before.channel.id in self.channel_owners
            and len(before.channel.members) == 0
        ):
            asyncio.create_task(self.schedule_deletion(before.channel))

        if not after or not after.channel:
            return

        guild = member.guild
        settings = await self.config.guild(guild).all()

        if not settings["auto_enabled"] or after.channel.id not in settings["auto_channels"]:
            return

        category = after.channel.category

        new_channel = await category.create_voice_channel(
            settings["name"],
            user_limit=min(settings["user_limit"] or 0, 99),
            reason="Auto voice channel creation",
        )

        try:
            overwrites_to_apply = {}
            for target, overwrite in after.channel.overwrites.items():
                overwrites_to_apply[target] = overwrite

            overwrites_to_apply[guild.me] = discord.PermissionOverwrite(
                manage_channels=True, manage_permissions=True, connect=True, view_channel=True
            )

            await new_channel.edit(overwrites=overwrites_to_apply)
        except Exception:
            pass
        await member.move_to(new_channel, reason="Moved to new voice room")
        await new_channel.set_permissions(member, view_channel=True, connect=True)
        self.channel_owners[new_channel.id] = member.id

        try:
            view = ChannelControlView(new_channel, member.id, self)
            control_message = await new_channel.send(
                embed=discord.Embed(
                    title="🔧 Voice Channel Controls",
                    description="Use the buttons below to control your channel.",
                    color=discord.Color.blurple(),
                ),
                view=view,
            )
            await self._save_room(new_channel, member.id, control_message.id)
            self.bot.dispatch("roomer_room_created", new_channel, member.id)
        except Exception:
            log.exception("Failed to initialize controls for room %s", new_channel.id)

    async def schedule_deletion(self, channel: discord.VoiceChannel):
        await asyncio.sleep(10)
        if channel and len(channel.members) == 0:
            try:
                announcer = self.bot.get_cog("RoomAnnounce")
                if announcer is not None:
                    await announcer.cleanup_room(channel)
                self.bot.dispatch("roomer_room_deleting", channel)
                await asyncio.sleep(0)
                await channel.delete(reason="Temporary voice channel expired")
            except discord.NotFound:
                pass
            except discord.Forbidden:
                pass
            finally:
                self.channel_owners.pop(channel.id, None)
                self.reminder_messages.pop(channel.id, None)
                async with self.config.guild(channel.guild).rooms() as rooms:
                    rooms.pop(str(channel.id), None)
        elif channel and len(channel.members) > 0:
            # Send a reminder to claim the room
            await self.send_claim_reminder(channel)


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
        await self.channel.edit(status=self.status.value)
        await interaction.response.send_message(
            f"✅ Channel status updated to **{self.status.value}**.", ephemeral=True
        )


class MentionableSelect(discord.ui.MentionableSelect):
    def __init__(self, channel: discord.VoiceChannel, action: str, channel_owners: dict):
        self.channel = channel
        self.action = action
        self.channel_owners = channel_owners
        super().__init__(placeholder="Select a user or role...", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        mentions = []
        current_owner = self.channel_owners.get(self.channel.id)
        for selected in self.values:
            target = None

            if isinstance(selected, discord.Member):
                if current_owner == selected.id:
                    await interaction.response.send_message(
                        "❌ You cannot modify your own permissions.", ephemeral=True
                    )
                    return
                else:
                    target = selected
            elif isinstance(selected, discord.Role):
                target = selected

            if target:
                if self.action == "permit":
                    await self.channel.set_permissions(target, connect=True, view_channel=True)
                    mentions.append(target.mention)
                elif self.action == "forbid":
                    await self.channel.set_permissions(target, connect=False)
                    mentions.append(target.mention)

        if mentions:
            action_text = "permitted" if self.action == "permit" else "forbidden"
            await interaction.response.send_message(
                f"✅ Updated permissions for: {', '.join(mentions)} ({action_text}).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ No valid users or roles were selected.", ephemeral=True
            )


class MentionableView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel, action: str, channel_owners: dict):
        super().__init__()
        self.add_item(MentionableSelect(channel, action, channel_owners))


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


class ApplyPresetSelect(discord.ui.Select):
    def __init__(self, channel: discord.VoiceChannel, presets: dict[str, dict], cog: Roomer):
        self.channel = channel
        self.presets = presets
        self.cog = cog
        options = [
            discord.SelectOption(label=name, description=data.get("title") or "No title")
            for name, data in presets.items()
        ]
        super().__init__(
            placeholder="Select a preset to apply",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        preset = self.presets.get(selected)
        if not preset:
            await interaction.response.send_message("❌ Preset not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.apply_room_preset(self.channel, selected)
            await interaction.followup.send(
                f"✅ Applied preset **{selected}** to the channel.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to apply preset: {e}", ephemeral=True)


class ChannelControlView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int, cog: Roomer):
        super().__init__(timeout=None)
        self.channel = channel
        self.owner_id = owner_id
        self.cog = cog

    async def _check_permissions(self, interaction: discord.Interaction):
        if not self.cog.is_room_owner(self.channel, interaction.user):
            await interaction.response.send_message(
                "❌ You must be the current owner and present in this voice channel.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="🔒 Lock", row=0, custom_id="roomer:lock", style=discord.ButtonStyle.danger
    )
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
        overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, connect=True)
        await self.channel.edit(overwrites=overwrites)

        await asyncio.sleep(0.1)  # Account for lag
        updated = self.channel.overwrites_for(self.channel.guild.default_role)
        locked = updated.connect is False

        button.label = "🔓 Unlock" if locked else "🔒 Lock"
        button.style = discord.ButtonStyle.success if locked else discord.ButtonStyle.danger

        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            "🔓 Channel unlocked." if currently_locked else "🔒 Channel locked.", ephemeral=True
        )

    @discord.ui.button(
        label="👁 Hide", row=0, custom_id="roomer:hide", style=discord.ButtonStyle.danger
    )
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
        overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, connect=True)
        await self.channel.edit(overwrites=overwrites)

        await asyncio.sleep(0.1)  # Account for lag
        updated = self.channel.overwrites_for(self.channel.guild.default_role)
        hidden = updated.view_channel is False

        button.label = "👁 Unhide" if hidden else "👁 Hide"
        button.style = discord.ButtonStyle.success if hidden else discord.ButtonStyle.danger

        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            (
                "👁 Channel is now visible to everyone."
                if currently_hidden
                else "🙈 Channel hidden from others."
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="➕ Permit", row=1, custom_id="roomer:permit", style=discord.ButtonStyle.success
    )
    async def permit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return

        view = MentionableView(
            self.channel, action="permit", channel_owners=self.cog.channel_owners
        )
        await interaction.response.send_message(
            "Select a user or role to permit:", view=view, ephemeral=True
        )

    @discord.ui.button(
        label="➖ Forbid", row=1, custom_id="roomer:forbid", style=discord.ButtonStyle.danger
    )
    async def forbid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return

        view = MentionableView(
            self.channel, action="forbid", channel_owners=self.cog.channel_owners
        )
        await interaction.response.send_message(
            "Select a user or role to forbid:", view=view, ephemeral=True
        )

    @discord.ui.button(
        label="✏️ Rename", row=0, custom_id="roomer:rename", style=discord.ButtonStyle.primary
    )
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        modal = RenameModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="📝 Set Status",
        row=2,
        custom_id="roomer:status",
        style=discord.ButtonStyle.secondary,
    )
    async def set_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        modal = SetStatusModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="👥 Set Limit", row=2, custom_id="roomer:limit", style=discord.ButtonStyle.secondary
    )
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        modal = LimitModal(self.channel)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="🔄 Reset Channel",
        row=3,
        custom_id="roomer:reset",
        style=discord.ButtonStyle.secondary,
    )
    async def reset_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return

        category = self.channel.category
        new_overwrites = category.overwrites if category else {}
        new_overwrites[interaction.user] = discord.PermissionOverwrite(
            view_channel=True, connect=True
        )

        await self.channel.edit(
            name="Voice Room", user_limit=0, status=None, overwrites=new_overwrites
        )
        await self.cog.set_room_preset(self.channel, None)

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "roomer:lock":
                    item.label = "🔒 Lock"
                    item.style = discord.ButtonStyle.danger
                elif item.custom_id == "roomer:hide":
                    item.label = "🙈 Hide"
                    item.style = discord.ButtonStyle.danger
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🔄 Channel reset to default settings.", ephemeral=True)

    @discord.ui.button(
        label="🧹 Clear Permissions",
        row=3,
        custom_id="roomer:clear_permissions",
        style=discord.ButtonStyle.secondary,
    )
    async def clear_permissions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return

        category = self.channel.category
        new_overwrites = category.overwrites if category else {}
        new_overwrites[interaction.user] = discord.PermissionOverwrite(
            view_channel=True, connect=True
        )

        try:
            await self.channel.edit(overwrites=new_overwrites)
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    if item.custom_id == "roomer:lock":
                        item.label = "🔒 Lock"
                        item.style = discord.ButtonStyle.danger
                    elif item.custom_id == "roomer:hide":
                        item.label = "👁 Hide"
                        item.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                "✅ All permission overwrites have been cleared.", ephemeral=True
            )
        except Exception as e:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"❌ Failed to clear permissions: {e}", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ Failed to clear permissions: {e}", ephemeral=True
                )

    @discord.ui.button(
        label="🎙 Claim Room",
        row=4,
        custom_id="roomer:claim",
        style=discord.ButtonStyle.secondary,
    )
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
                if current_owner and current_owner != self.channel.guild.me:
                    await self.channel.set_permissions(current_owner, overwrite=None)
                await self.channel.set_permissions(
                    interaction.user, view_channel=True, connect=True
                )
                self.owner_id = interaction.user.id
                await cog.set_room_owner(self.channel, interaction.user.id)
                reminder_message = cog.reminder_messages.pop(self.channel.id, None)
                if reminder_message:
                    try:
                        await reminder_message.delete()
                    except discord.NotFound:
                        pass
                await interaction.response.send_message(
                    "✅ You have claimed ownership of this room.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ The current owner is still in the room.", ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(f"❌ Claim failed: {e}", ephemeral=True)

    @discord.ui.button(
        label="🎮 Channel Preset",
        row=4,
        style=discord.ButtonStyle.secondary,
        custom_id="roomer:preset",
    )
    async def apply_preset(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permissions(interaction):
            return
        try:
            config = interaction.client.get_cog("Roomer").config
            presets = await config.guild(interaction.guild).presets()
            if not presets:
                await interaction.response.send_message("❌ No presets available.", ephemeral=True)
                return
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to execute command: {e}", ephemeral=True
            )
            return
        view = discord.ui.View()
        view.add_item(ApplyPresetSelect(self.channel, presets, self.cog))
        try:
            await interaction.response.send_message(
                "🎮 Select a preset to apply:", view=view, ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Failed to send message: {e}", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Roomer(bot))
