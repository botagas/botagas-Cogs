import asyncio
import contextlib
import logging
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands

from .models import (
    MISSING_GAME,
    MISSING_PARTY,
    MISSING_ROLE,
    automatic_metadata_ready,
    default_room_state,
    game_names_match,
    has_game_context,
    normalize_game_name,
    preview_should_be_visible,
    resolve_fields,
)
from .providers import MetadataProviderError, ProviderHub
from .views import (
    AnnouncementControlView,
    GameChoiceView,
    MetadataChoiceView,
    RoleChoiceView,
)

log = logging.getLogger("red.botagas.roomannounce")


class RoomAnnounce(commands.Cog):
    """Companion announcements for Roomer voice channels."""

    roomannounce_group = app_commands.Group(
        name="roomannounce", description="Configure Roomer game announcements."
    )

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=300620201744, force_registration=True)
        self.config.register_guild(
            announcement_channel_id=None,
            auto_announce=False,
            auto_tag=False,
            igdb_enabled=False,
            steamgriddb_enabled=False,
            allowed_role_ids=[],
        )
        channel_defaults = default_room_state(0)
        channel_defaults["owner_id"] = None
        self.config.register_channel(**channel_defaults)
        self.session = aiohttp.ClientSession()
        self.providers = ProviderHub(bot, self.session)
        self._presence_tasks: Dict[int, asyncio.Task] = {}
        self._initialize_task = asyncio.create_task(self._initialize())

    async def cog_unload(self):
        self._initialize_task.cancel()
        for task in self._presence_tasks.values():
            task.cancel()
        await self.session.close()

    async def _initialize(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(1)
        roomer = self.bot.get_cog("Roomer")
        if roomer is None:
            log.warning("RoomAnnounce loaded without Roomer; waiting for room lifecycle events.")
            return
        for guild_id, data in (await roomer.config.all_guilds()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            for channel_id, room in data.get("rooms", {}).items():
                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.VoiceChannel):
                    await self.config.channel_from_id(int(channel_id)).clear()
                    continue
                await self.ensure_room(
                    channel,
                    room.get("owner_id"),
                    selected_preset=room.get("selected_preset"),
                    restore=True,
                )

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        for channel_id, state in (await self.config.all_channels()).items():
            if state.get("owner_id") == user_id:
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.VoiceChannel):
                    await self.cleanup_room(channel)
                else:
                    await self.config.channel_from_id(channel_id).clear()

    def _roomer(self):
        return self.bot.get_cog("Roomer")

    def _active_guild_rooms(self, guild: discord.Guild) -> List[discord.VoiceChannel]:
        roomer = self._roomer()
        if roomer is None:
            return []
        return [
            channel
            for channel_id in roomer.channel_owners
            if isinstance((channel := guild.get_channel(channel_id)), discord.VoiceChannel)
        ]

    async def get_state(self, channel_id: int) -> Dict[str, Any]:
        return await self.config.channel_from_id(channel_id).all()

    async def _save_state(self, channel_id: int, state: Dict[str, Any]) -> None:
        await self.config.channel_from_id(channel_id).set(state)

    async def check_owner(self, interaction: discord.Interaction, channel_id: int) -> bool:
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        roomer = self._roomer()
        allowed = bool(
            isinstance(channel, discord.VoiceChannel)
            and roomer
            and roomer.is_room_owner(channel, interaction.user)
        )
        if not allowed and not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ You must be the current room owner and present in the voice channel.",
                ephemeral=True,
            )
        return allowed

    async def _fetch_message(
        self, channel: discord.abc.Messageable, message_id: Optional[int]
    ) -> Optional[discord.Message]:
        if not message_id:
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            return None

    async def ensure_room(
        self,
        channel: discord.VoiceChannel,
        owner_id: Optional[int],
        selected_preset: Optional[str] = None,
        restore: bool = False,
    ) -> None:
        if not owner_id:
            return
        state = await self.get_state(channel.id)
        if not state.get("owner_id"):
            state = default_room_state(owner_id)
        state["owner_id"] = owner_id
        if selected_preset is not None:
            state["selected_preset"] = selected_preset

        control_message = await self._fetch_message(channel, state.get("control_message_id"))
        preview_visible = bool(await self._fetch_message(channel, state.get("preview_message_id")))
        view = AnnouncementControlView(
            self,
            channel.id,
            state.get("announcements_enabled", True),
            preview_visible,
        )
        if control_message is None:
            control_message = await channel.send(
                embed=discord.Embed(
                    title="📣 Announcement Controls",
                    description="Use these controls to prepare and publish this room's game announcement.",
                    color=discord.Color.blurple(),
                ),
                view=view,
            )
            state["control_message_id"] = control_message.id
        elif restore:
            self.bot.add_view(view, message_id=control_message.id)
        await self._save_state(channel.id, state)
        owner = channel.guild.get_member(owner_id)
        if owner:
            detected = self._extract_activity(owner)
            if detected:
                state["detected"] = detected
                await self._save_state(channel.id, state)
        await self.resolve_room(channel)

    def _extract_activity(self, member: discord.Member) -> Dict[str, Any]:
        activities = [
            activity
            for activity in getattr(member, "activities", ())
            if getattr(activity, "type", None) == discord.ActivityType.playing
            and getattr(activity, "name", None)
        ]
        if not activities:
            return {}
        activity = max(
            activities,
            key=lambda item: bool(getattr(item, "application_id", None))
            + bool(getattr(item, "details", None)),
        )
        details = getattr(activity, "details", None) or getattr(activity, "state", None) or ""
        party_data = getattr(activity, "party", None) or {}
        party_size = party_data.get("size") if isinstance(party_data, dict) else None
        party = ""
        if party_size and len(party_size) == 2:
            party = f"{party_size[0]}/{party_size[1]}"
        image_url = ""
        with contextlib.suppress(Exception):
            image_url = str(activity.large_image_url or "")
        return {
            "name": activity.name,
            "description": details,
            "party": party,
            "image_url": image_url,
            "application_id": getattr(activity, "application_id", None),
        }

    async def _preset_for_state(
        self, channel: discord.VoiceChannel, state: Dict[str, Any]
    ) -> tuple[Optional[str], Dict[str, Any], Dict[str, Dict[str, Any]]]:
        roomer = self._roomer()
        presets = await roomer.config.guild(channel.guild).presets() if roomer else {}
        name = state.get("selected_preset")
        return name, presets.get(name, {}) if name else {}, presets

    async def _resolve_provider(
        self,
        channel: discord.VoiceChannel,
        game_name: str,
        settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        provider: Dict[str, Any] = {}
        if settings.get("igdb_enabled"):
            try:
                provider = await self.providers.exact_igdb(game_name) or {}
            except MetadataProviderError as exc:
                log.info("IGDB lookup unavailable for %s: %s", game_name, exc)
        if settings.get("steamgriddb_enabled") and not provider.get("image_url"):
            try:
                artwork = await self.providers.steamgriddb_art(game_name)
            except MetadataProviderError as exc:
                log.info("SteamGridDB lookup unavailable for %s: %s", game_name, exc)
            else:
                if artwork:
                    provider["image_url"] = artwork
                    provider.setdefault("name", game_name)
                    provider.setdefault("source", "SteamGridDB")
        return provider

    async def resolve_room(self, channel: discord.VoiceChannel) -> None:
        state = await self.get_state(channel.id)
        preset_name, preset, presets = await self._preset_for_state(channel, state)
        detected = state.get("detected") or {}
        settings = await self.config.guild(channel.guild).all()

        manual = state.get("manual_overrides") or {}
        if state.get("source_choice") == "manual":
            initial_game = (
                manual.get("game_name") or preset.get("game_name") or detected.get("name")
            )
        else:
            initial_game = (
                preset.get("game_name") or detected.get("name") or manual.get("game_name")
            )
        provider = state.get("provider") or {}
        if initial_game and not game_names_match(provider.get("name"), [initial_game]):
            provider = await self._resolve_provider(channel, initial_game, settings)
        state["provider"] = provider

        mapped_role_id = state.get("selected_role_id")
        if not mapped_role_id and detected.get("name") and not preset:
            for candidate in presets.values():
                if game_names_match(
                    detected["name"],
                    [candidate.get("game_name"), *(candidate.get("game_aliases") or [])],
                ):
                    mapped_role_id = candidate.get("announcement_role_id")
                    break

        resolved = resolve_fields(
            preset_name,
            preset,
            detected,
            provider,
            manual,
            mapped_role_id,
        )
        old_identity = state.get("identity")
        had_public_announcement = bool(state.get("public_message_id"))
        new_identity = resolved.get("identity")
        if old_identity and new_identity != old_identity:
            await self._delete_public(channel, state, suppress=False)
            state["suppressed_identity"] = None
            state["missing_role_warned_identity"] = None
            state["tagged_identity"] = None
        state["identity"] = new_identity
        state["resolved"] = resolved
        state["auto_eligible"] = automatic_metadata_ready(resolved, preset, detected, provider)
        await self._save_state(channel.id, state)
        await self.ensure_preview(channel)
        await self.refresh_control(channel)

        should_publish = (had_public_announcement or settings.get("auto_announce")) and (
            state.get("announcements_enabled")
            and (had_public_announcement or state.get("auto_eligible"))
            and new_identity
            and state.get("suppressed_identity") != new_identity
        )
        if should_publish:
            try:
                await self.publish_room(channel, tag_requested=settings.get("auto_tag", False))
            except (RuntimeError, discord.Forbidden, discord.HTTPException) as exc:
                state = await self.get_state(channel.id)
                state["last_error"] = str(exc)
                await self._save_state(channel.id, state)
                await self.ensure_preview(channel)

    def _preview_should_be_visible(self, state: Dict[str, Any]) -> bool:
        return preview_should_be_visible(state)

    def _build_embed(
        self, channel: discord.VoiceChannel, state: Dict[str, Any], public: bool = False
    ) -> discord.Embed:
        resolved = state.get("resolved") or {}
        game_name = resolved.get("game_name") or MISSING_GAME
        title = f"🎮 {game_name}" if public else "🎮 Room Announcement Preview"
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        if public:
            embed.description = (resolved.get("description") or "Join the room to play!")[:2000]
        else:
            embed.add_field(name="Game", value=game_name, inline=False)
            embed.add_field(name="Source", value=resolved.get("source") or "None", inline=True)
            embed.add_field(
                name="Description",
                value=(resolved.get("description") or "Missing — enter manually or use metadata")[
                    :1024
                ],
                inline=False,
            )
        embed.add_field(name="Game party", value=resolved.get("party") or MISSING_PARTY)
        voice_limit = channel.user_limit or "unlimited"
        embed.add_field(name="Voice room", value=f"{len(channel.members)}/{voice_limit}")
        role = channel.guild.get_role(resolved.get("role_id")) if resolved.get("role_id") else None
        embed.add_field(name="Announcement role", value=role.mention if role else MISSING_ROLE)
        embed.add_field(name="Voice channel", value=channel.mention)
        if resolved.get("note"):
            embed.add_field(name="Note", value=resolved["note"][:1024], inline=False)
        if not public:
            if state.get("last_error"):
                status = f"Error — {state['last_error']}"
            elif not state.get("announcements_enabled"):
                status = "Disabled for this room"
            elif state.get("public_message_id"):
                status = "Published"
            elif state.get("auto_eligible"):
                status = "Ready"
            else:
                status = "Needs review"
            embed.add_field(name="Public announcement", value=status, inline=False)
            if resolved.get("detected_conflict"):
                embed.add_field(
                    name="Detected game differs",
                    value=f"Discord detected **{resolved.get('detected_game')}**; the preset remains active.",
                    inline=False,
                )
        if resolved.get("provider_url"):
            embed.add_field(name="Game information", value=resolved["provider_url"], inline=False)
        if resolved.get("image_url", "").startswith("https://"):
            embed.set_image(url=resolved["image_url"])
        owner = channel.guild.get_member(state.get("owner_id"))
        if owner:
            embed.set_footer(text=f"Room owner: {owner.display_name}")
        return embed

    async def ensure_preview(self, channel: discord.VoiceChannel) -> None:
        state = await self.get_state(channel.id)
        message = await self._fetch_message(channel, state.get("preview_message_id"))
        if self._preview_should_be_visible(state):
            embed = self._build_embed(channel, state)
            if message:
                await message.edit(embed=embed)
            else:
                message = await channel.send(embed=embed)
                state["preview_message_id"] = message.id
                await self._save_state(channel.id, state)
        elif message:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()
            state["preview_message_id"] = None
            await self._save_state(channel.id, state)

    async def refresh_control(self, channel: discord.VoiceChannel) -> None:
        state = await self.get_state(channel.id)
        message = await self._fetch_message(channel, state.get("control_message_id"))
        if message:
            await message.edit(
                view=AnnouncementControlView(
                    self,
                    channel.id,
                    state.get("announcements_enabled", True),
                    bool(await self._fetch_message(channel, state.get("preview_message_id"))),
                )
            )

    async def _warn_missing_role(
        self, channel: discord.VoiceChannel, state: Dict[str, Any]
    ) -> None:
        identity = state.get("identity")
        if not identity or state.get("missing_role_warned_identity") == identity:
            return
        await channel.send(
            "⚠️ No matching preset or approved announcement role was found, so no role was tagged.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        state["missing_role_warned_identity"] = identity
        await self._save_state(channel.id, state)

    async def publish_room(
        self, channel: discord.VoiceChannel, tag_requested: bool = False
    ) -> discord.Message:
        state = await self.get_state(channel.id)
        if not state.get("announcements_enabled"):
            raise RuntimeError("Announcements are disabled for this room.")
        if not has_game_context(state.get("resolved") or {}):
            raise RuntimeError("No game is configured yet.")
        guild_settings = await self.config.guild(channel.guild).all()
        destination_id = guild_settings.get("announcement_channel_id")
        destination = channel.guild.get_channel(destination_id) if destination_id else None
        if not isinstance(destination, discord.TextChannel):
            raise RuntimeError("The announcement channel is not configured or no longer exists.")

        role_id = (state.get("resolved") or {}).get("role_id")
        role = channel.guild.get_role(role_id) if role_id else None
        should_tag = bool(
            tag_requested and role and state.get("tagged_identity") != state.get("identity")
        )
        if tag_requested and role is None:
            await self._warn_missing_role(channel, state)
            state = await self.get_state(channel.id)
        elif should_tag and not (
            role.mentionable or destination.permissions_for(channel.guild.me).mention_everyone
        ):
            if state.get("missing_role_warned_identity") != state.get("identity"):
                await channel.send(
                    f"⚠️ {role.mention} cannot be tagged because it is not mentionable and the bot lacks permission to mention roles.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                state["missing_role_warned_identity"] = state.get("identity")
                await self._save_state(channel.id, state)
            should_tag = False

        existing = await self._fetch_message(destination, state.get("public_message_id"))
        if existing and should_tag:
            with contextlib.suppress(discord.HTTPException):
                await existing.delete()
            existing = None
            state["public_message_id"] = None

        embed = self._build_embed(channel, state, public=True)
        if existing:
            await existing.edit(
                content="", embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
            message = existing
        else:
            content = role.mention if should_tag else None
            allowed_mentions = (
                discord.AllowedMentions(roles=[role], users=False, everyone=False)
                if should_tag
                else discord.AllowedMentions.none()
            )
            message = await destination.send(
                content=content, embed=embed, allowed_mentions=allowed_mentions
            )
        state["public_message_id"] = message.id
        state["public_channel_id"] = destination.id
        state["suppressed_identity"] = None
        state["last_error"] = None
        if should_tag:
            state["tagged_identity"] = state.get("identity")
        await self._save_state(channel.id, state)
        await self.ensure_preview(channel)
        return message

    async def _delete_public(
        self, channel: discord.VoiceChannel, state: Dict[str, Any], suppress: bool
    ) -> None:
        destination = channel.guild.get_channel(state.get("public_channel_id"))
        message = (
            await self._fetch_message(destination, state.get("public_message_id"))
            if destination
            else None
        )
        if message:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()
        state["public_message_id"] = None
        state["public_channel_id"] = None
        if suppress:
            state["suppressed_identity"] = state.get("identity")
        await self._save_state(channel.id, state)

    async def cleanup_room(self, channel: discord.VoiceChannel) -> None:
        state = await self.get_state(channel.id)
        await self._delete_public(channel, state, suppress=False)
        preview = await self._fetch_message(channel, state.get("preview_message_id"))
        if preview:
            with contextlib.suppress(discord.HTTPException):
                await preview.delete()
        await self.config.channel(channel).clear()

    async def _game_choice_options(
        self, guild: discord.Guild, channel_id: int
    ) -> List[discord.SelectOption]:
        state = await self.get_state(channel_id)
        roomer = self._roomer()
        presets = await roomer.config.guild(guild).presets() if roomer else {}
        options: List[discord.SelectOption] = []
        for name, preset in list(presets.items())[:23]:
            options.append(
                discord.SelectOption(
                    label=f"Preset: {name}"[:100],
                    value=f"preset:{name}",
                    description=(preset.get("game_name") or preset.get("title") or "Preset")[:100],
                )
            )
        detected = (state.get("detected") or {}).get("name")
        if detected:
            options.append(
                discord.SelectOption(label=f"Detected: {detected}"[:100], value="detected")
            )
        options.append(discord.SelectOption(label="Enter manually", value="manual"))
        return options

    async def show_game_choices(self, interaction: discord.Interaction, channel_id: int) -> None:
        channel = interaction.guild.get_channel(channel_id)
        options = await self._game_choice_options(interaction.guild, channel_id)
        await interaction.response.send_message(
            "Choose the game information source:",
            view=GameChoiceView(self, channel.id, options),
            ephemeral=True,
        )

    async def choose_game_source(
        self, interaction: discord.Interaction, channel_id: int, selected: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        state = await self.get_state(channel_id)
        roomer = self._roomer()
        if selected.startswith("preset:"):
            preset_name = selected.split(":", 1)[1]
            state["selected_preset"] = preset_name
            state["source_choice"] = "preset"
            await self._save_state(channel_id, state)
            if roomer:
                await roomer.set_room_preset(channel, preset_name)
        elif selected == "detected":
            state["selected_preset"] = None
            state["source_choice"] = "detected"
            state["manual_overrides"].pop("game_name", None)
            await self._save_state(channel_id, state)
            if roomer:
                await roomer.set_room_preset(channel, None)
        await self.resolve_room(channel)
        await interaction.followup.send("✅ Game source updated.", ephemeral=True)

    async def apply_manual_details(
        self, interaction: discord.Interaction, channel_id: int, values: Dict[str, str]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        state = await self.get_state(channel_id)
        state["manual_overrides"].update(values)
        state["source_choice"] = "manual"
        await self._save_state(channel_id, state)
        settings = await self.config.guild(interaction.guild).all()
        if settings.get("igdb_enabled") and values.get("game_name"):
            try:
                candidates = await self.providers.search_igdb(values["game_name"])
            except MetadataProviderError:
                candidates = []
            exact = [
                item
                for item in candidates
                if game_names_match(
                    values["game_name"], [item.get("name"), *(item.get("aliases") or [])]
                )
            ]
            if len(exact) == 1:
                state["provider"] = exact[0]
                await self._save_state(channel_id, state)
            elif len(candidates) > 1:
                await self.resolve_room(channel)
                return await interaction.followup.send(
                    "Choose the matching metadata result:",
                    view=MetadataChoiceView(self, channel_id, candidates),
                    ephemeral=True,
                )
        await self.resolve_room(channel)
        await interaction.followup.send("✅ Announcement details updated.", ephemeral=True)

    async def select_metadata_candidate(
        self, interaction: discord.Interaction, channel_id: int, candidate: Dict[str, Any]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = await self.get_state(channel_id)
        state["provider"] = candidate
        state.setdefault("manual_overrides", {})["game_name"] = candidate.get("name")
        await self._save_state(channel_id, state)
        channel = interaction.guild.get_channel(channel_id)
        await self.resolve_room(channel)
        await interaction.followup.send("✅ Metadata selected.", ephemeral=True)

    async def show_role_choices(self, interaction: discord.Interaction, channel_id: int) -> None:
        allowed_ids = await self.config.guild(interaction.guild).allowed_role_ids()
        roomer = self._roomer()
        if roomer:
            presets = await roomer.config.guild(interaction.guild).presets()
            for preset in presets.values():
                role_id = preset.get("announcement_role_id")
                if role_id and role_id not in allowed_ids:
                    allowed_ids.append(role_id)
        roles = [interaction.guild.get_role(role_id) for role_id in allowed_ids]
        roles = [role for role in roles if role is not None]
        await interaction.response.send_message(
            "Choose an approved role. Selecting one also republishes the current announcement with a tag.",
            view=RoleChoiceView(self, channel_id, roles),
            ephemeral=True,
        )

    async def select_role(
        self, interaction: discord.Interaction, channel_id: int, role_id: Optional[int]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = await self.get_state(channel_id)
        state["selected_role_id"] = role_id
        await self._save_state(channel_id, state)
        channel = interaction.guild.get_channel(channel_id)
        await self.resolve_room(channel)
        state = await self.get_state(channel_id)
        if (
            role_id
            and state.get("announcements_enabled")
            and has_game_context(state.get("resolved", {}))
        ):
            try:
                await self.publish_room(channel, tag_requested=True)
            except RuntimeError as exc:
                return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
        await interaction.followup.send("✅ Announcement role updated.", ephemeral=True)

    async def publish_from_interaction(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        try:
            message = await self.publish_room(channel)
        except (RuntimeError, discord.HTTPException, discord.Forbidden) as exc:
            state = await self.get_state(channel_id)
            state["last_error"] = str(exc)
            await self._save_state(channel_id, state)
            await self.ensure_preview(channel)
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(
            f"✅ Announcement published: {message.jump_url}", ephemeral=True
        )

    async def refresh_from_interaction(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        state = await self.get_state(channel_id)
        owner = interaction.guild.get_member(state.get("owner_id"))
        state["detected"] = self._extract_activity(owner) if owner else {}
        await self._save_state(channel_id, state)
        await self.resolve_room(channel)
        await interaction.followup.send("✅ Announcement data refreshed.", ephemeral=True)

    async def toggle_announcements(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        state = await self.get_state(channel_id)
        enabled = not state.get("announcements_enabled", True)
        state["announcements_enabled"] = enabled
        state["preview_mode"] = "auto"
        if not enabled:
            await self._delete_public(channel, state, suppress=False)
            state = await self.get_state(channel_id)
        await self._save_state(channel_id, state)
        await self.resolve_room(channel)
        await interaction.followup.send(
            f"✅ Announcements {'enabled' if enabled else 'disabled'} for this room.",
            ephemeral=True,
        )

    async def toggle_preview(self, interaction: discord.Interaction, channel_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        state = await self.get_state(channel_id)
        visible = bool(await self._fetch_message(channel, state.get("preview_message_id")))
        if not visible and not has_game_context(state.get("resolved") or {}):
            options = await self._game_choice_options(interaction.guild, channel_id)
            await interaction.followup.send(
                "No game data exists yet. Choose a preset, detected game, or manual entry first.",
                view=GameChoiceView(self, channel_id, options),
                ephemeral=True,
            )
            return
        state["preview_mode"] = "hidden" if visible else "visible"
        await self._save_state(channel_id, state)
        await self.ensure_preview(channel)
        await self.refresh_control(channel)
        await interaction.followup.send(
            f"✅ Preview {'hidden' if visible else 'shown'}.", ephemeral=True
        )

    async def delete_public_from_interaction(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        state = await self.get_state(channel_id)
        await self._delete_public(channel, state, suppress=True)
        await self.ensure_preview(channel)
        await interaction.followup.send("✅ Public announcement deleted.", ephemeral=True)

    async def reset_overrides(self, interaction: discord.Interaction, channel_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        state = await self.get_state(channel_id)
        state["manual_overrides"] = {}
        await self._save_state(channel_id, state)
        channel = interaction.guild.get_channel(channel_id)
        await self.resolve_room(channel)
        await interaction.followup.send("✅ Manual overrides reset.", ephemeral=True)

    @commands.Cog.listener()
    async def on_roomer_room_created(self, channel: discord.VoiceChannel, owner_id: int):
        await self.ensure_room(channel, owner_id)

    @commands.Cog.listener()
    async def on_roomer_owner_changed(self, channel: discord.VoiceChannel, owner_id: int):
        state = await self.get_state(channel.id)
        state["owner_id"] = owner_id
        owner = channel.guild.get_member(owner_id)
        state["detected"] = self._extract_activity(owner) if owner else {}
        await self._save_state(channel.id, state)
        await self.resolve_room(channel)

    @commands.Cog.listener()
    async def on_roomer_preset_applied(
        self,
        channel: discord.VoiceChannel,
        preset_name: Optional[str],
        preset: Dict[str, Any],
    ):
        state = await self.get_state(channel.id)
        state["selected_preset"] = preset_name
        if preset_name:
            state["source_choice"] = "preset"
            state.setdefault("manual_overrides", {}).pop("game_name", None)
        elif state.get("source_choice") == "preset":
            state["source_choice"] = "auto"
        await self._save_state(channel.id, state)
        await self.resolve_room(channel)

    @commands.Cog.listener()
    async def on_roomer_room_deleting(self, channel: discord.VoiceChannel):
        await self.cleanup_room(channel)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        roomer = self._roomer()
        if roomer is None:
            return
        channel_id = next(
            (
                room_id
                for room_id, owner_id in roomer.channel_owners.items()
                if owner_id == after.id
            ),
            None,
        )
        if channel_id is None:
            return
        old_task = self._presence_tasks.pop(channel_id, None)
        if old_task:
            old_task.cancel()
        self._presence_tasks[channel_id] = asyncio.create_task(
            self._apply_presence_after_delay(channel_id, after)
        )

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens):
        if service_name in {"twitch", "steamgriddb"}:
            self.providers.invalidate(service_name)

    async def _apply_presence_after_delay(self, channel_id: int, member: discord.Member):
        try:
            await asyncio.sleep(30)
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                return
            state = await self.get_state(channel_id)
            state["detected"] = self._extract_activity(member)
            await self._save_state(channel_id, state)
            await self.resolve_room(channel)
        except asyncio.CancelledError:
            return
        finally:
            if self._presence_tasks.get(channel_id) is asyncio.current_task():
                self._presence_tasks.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        roomer = self._roomer()
        if roomer is None:
            return
        channel_ids = {
            channel.id
            for channel in (before.channel, after.channel)
            if channel and channel.id in roomer.channel_owners
        }
        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                await self.ensure_preview(channel)
                state = await self.get_state(channel_id)
                if state.get("public_message_id"):
                    with contextlib.suppress(RuntimeError, discord.HTTPException):
                        await self.publish_room(channel)

    @roomannounce_group.command(name="channel", description="Set the public announcement channel.")
    @app_commands.default_permissions(administrator=True)
    async def channel_command(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]
    ):
        await interaction.response.defer(ephemeral=True)
        rooms_to_republish = []
        for room in self._active_guild_rooms(interaction.guild):
            state = await self.get_state(room.id)
            if state.get("public_message_id"):
                rooms_to_republish.append(room)
                await self._delete_public(room, state, suppress=False)
        await self.config.guild(interaction.guild).announcement_channel_id.set(
            channel.id if channel else None
        )
        if channel:
            for room in rooms_to_republish:
                with contextlib.suppress(RuntimeError, discord.HTTPException):
                    await self.publish_room(room)
        await interaction.followup.send(
            f"✅ Announcement channel {'set to ' + channel.mention if channel else 'cleared'}.",
            ephemeral=True,
        )

    @roomannounce_group.command(name="autoannounce", description="Toggle automatic publishing.")
    @app_commands.default_permissions(administrator=True)
    async def autoannounce(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.config.guild(interaction.guild).auto_announce.set(enabled)
        if enabled:
            for room in self._active_guild_rooms(interaction.guild):
                await self.resolve_room(room)
        await interaction.followup.send(
            f"✅ Automatic announcements {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @roomannounce_group.command(name="autotag", description="Toggle automatic role tagging.")
    @app_commands.default_permissions(administrator=True)
    async def autotag(self, interaction: discord.Interaction, enabled: bool):
        await self.config.guild(interaction.guild).auto_tag.set(enabled)
        await interaction.response.send_message(
            f"✅ Automatic role tagging {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @roomannounce_group.command(name="provider", description="Enable a metadata provider.")
    @app_commands.choices(
        provider=[
            app_commands.Choice(name="IGDB", value="igdb"),
            app_commands.Choice(name="SteamGridDB", value="steamgriddb"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def provider(
        self,
        interaction: discord.Interaction,
        provider: app_commands.Choice[str],
        enabled: bool,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.config.guild(interaction.guild).set_raw(
            f"{provider.value}_enabled", value=enabled
        )
        for room in self._active_guild_rooms(interaction.guild):
            state = await self.get_state(room.id)
            state["provider"] = {}
            await self._save_state(room.id, state)
            await self.resolve_room(room)
        await interaction.followup.send(
            f"✅ {provider.name} {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @roomannounce_group.command(name="role", description="Manage approved announcement roles.")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def role(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        role: Optional[discord.Role] = None,
    ):
        role_ids = await self.config.guild(interaction.guild).allowed_role_ids()
        if action.value == "list":
            roles = [interaction.guild.get_role(role_id) for role_id in role_ids]
            value = ", ".join(item.mention for item in roles if item) or "None"
            return await interaction.response.send_message(
                f"Approved announcement roles: {value}", ephemeral=True
            )
        if role is None:
            return await interaction.response.send_message(
                "❌ Select a role for this action.", ephemeral=True
            )
        if action.value == "add":
            if role.id not in role_ids:
                if len(role_ids) >= 24:
                    return await interaction.response.send_message(
                        "❌ At most 24 approved roles are supported so the selector can include a clear option.",
                        ephemeral=True,
                    )
                role_ids.append(role.id)
        elif role.id in role_ids:
            role_ids.remove(role.id)
        await self.config.guild(interaction.guild).allowed_role_ids.set(role_ids)
        await interaction.response.send_message(
            f"✅ {role.mention} {'added to' if action.value == 'add' else 'removed from'} approved roles.",
            ephemeral=True,
        )

    @roomannounce_group.command(name="settings", description="Show announcement settings.")
    @app_commands.default_permissions(administrator=True)
    async def settings(self, interaction: discord.Interaction):
        data = await self.config.guild(interaction.guild).all()
        channel = interaction.guild.get_channel(data["announcement_channel_id"])
        roles = [interaction.guild.get_role(role_id) for role_id in data["allowed_role_ids"]]
        embed = discord.Embed(title="RoomAnnounce Settings", color=discord.Color.blurple())
        embed.add_field(name="Channel", value=channel.mention if channel else "Not configured")
        embed.add_field(name="Automatic announce", value=str(data["auto_announce"]))
        embed.add_field(name="Automatic tag", value=str(data["auto_tag"]))
        embed.add_field(name="IGDB", value=str(data["igdb_enabled"]))
        embed.add_field(name="SteamGridDB", value=str(data["steamgriddb_enabled"]))
        embed.add_field(
            name="Approved roles",
            value=", ".join(role.mention for role in roles if role) or "None",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RoomAnnounce(bot))
