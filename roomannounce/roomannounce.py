import asyncio
import contextlib
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import available_timezones

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands

from .models import (
    MISSING_GAME,
    MISSING_PARTY,
    RSVP_STATUSES,
    active_hours_are_active,
    active_hours_days_text,
    announcement_destination_id,
    automatic_metadata_ready,
    consume_rsvp_milestones,
    default_room_state,
    first_sentence,
    format_room_size,
    game_names_match,
    has_game_context,
    is_new_game_post,
    normalize_game_name,
    parse_clock,
    parse_weekdays,
    preset_game_name,
    preview_should_be_visible,
    resolve_fields,
    rsvp_groups,
    tagging_is_allowed,
    validate_timezone,
)
from .providers import MetadataProviderError, ProviderHub
from .views import (
    AnnouncementControlView,
    GameChoiceView,
    MetadataChoiceView,
    MonitoredChannelView,
    RoleChoiceView,
    RSVPView,
)

log = logging.getLogger("red.botagas.roomannounce")


class RoomAnnounce(commands.Cog):
    """Game announcements for Roomer and monitored voice channels."""

    roomannounce_group = app_commands.Group(
        name="roomannounce", description="Configure voice-channel game announcements."
    )
    activehours_group = app_commands.Group(
        name="activehours",
        description="Configure scheduled announcement tagging hours.",
        parent=roomannounce_group,
    )
    rsvp_group = app_commands.Group(
        name="rsvp",
        description="Configure announcement RSVP controls.",
        parent=roomannounce_group,
    )
    monitor_group = app_commands.Group(
        name="monitor",
        description="Configure informational announcements for static voice channels.",
        parent=roomannounce_group,
    )

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=300620201744, force_registration=True)
        self.config.register_guild(
            announcement_channel_id=None,
            announcement_channels={},
            auto_announce=False,
            auto_tag=False,
            igdb_enabled=False,
            steamgriddb_enabled=False,
            allowed_role_ids=[],
            active_hours_enabled=False,
            active_hours_timezone="",
            active_hours_start="",
            active_hours_end="",
            active_hours_weekdays=list(range(7)),
            active_hours_forced=False,
            rsvp_enabled=False,
            rsvp_show_names=False,
            monitored_channels={},
        )
        channel_defaults = default_room_state(0)
        channel_defaults["owner_id"] = None
        self.config.register_channel(**channel_defaults)
        self.session = aiohttp.ClientSession()
        self.providers = ProviderHub(bot, self.session)
        self._timezones = sorted(available_timezones())
        self._presence_tasks: Dict[int, asyncio.Task] = {}
        self._monitor_tasks: Dict[int, asyncio.Task] = {}
        self._monitor_locks: Dict[int, asyncio.Lock] = {}
        self._rsvp_locks: Dict[int, asyncio.Lock] = {}
        self._initialize_task = asyncio.create_task(self._initialize())

    async def cog_unload(self):
        self._initialize_task.cancel()
        for task in self._presence_tasks.values():
            task.cancel()
        for task in self._monitor_tasks.values():
            task.cancel()
        self._monitor_locks.clear()
        self._rsvp_locks.clear()
        await self.session.close()

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        command_name = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
        guild_id = getattr(getattr(interaction, "guild", None), "id", None)
        user_id = getattr(getattr(interaction, "user", None), "id", None)
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        log.error(
            "RoomAnnounce application command failed: command=%s guild=%s user=%s",
            command_name,
            guild_id,
            user_id,
            exc_info=(type(original), original, original.__traceback__),
        )
        message = (
            "❌ You do not have permission to use this command."
            if isinstance(error, app_commands.CheckFailure)
            else "❌ RoomAnnounce could not complete that command. The failure was logged."
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            log.warning(
                "Could not deliver application-command error response for %s", command_name
            )

    async def _initialize(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(1)
        roomer = self.bot.get_cog("Roomer")
        if roomer is None:
            log.warning("RoomAnnounce loaded without Roomer; waiting for room lifecycle events.")
        else:
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
                        source_channel_id=room.get("source_channel_id"),
                        restore=True,
                    )
        await self._restore_monitored_channels()

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        for channel_id, state in (await self.config.all_channels()).items():
            if state.get("owner_id") == user_id:
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.VoiceChannel):
                    await self.cleanup_room(channel)
                else:
                    await self.config.channel_from_id(channel_id).clear()
                continue
            responses = state.get("rsvp_responses") or {}
            if str(user_id) not in responses:
                continue
            async with self._rsvp_lock(channel_id):
                state = await self.get_state(channel_id)
                responses = state.get("rsvp_responses") or {}
                responses.pop(str(user_id), None)
                state["rsvp_responses"] = responses
                await self._save_state(channel_id, state)
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.VoiceChannel) and state.get("public_message_id"):
                    with contextlib.suppress(
                        RuntimeError, discord.HTTPException, discord.Forbidden
                    ):
                        await self.publish_room(channel)

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

    @staticmethod
    def _room_access_state(channel: discord.VoiceChannel) -> tuple[bool, bool]:
        overwrite = channel.overwrites_for(channel.guild.default_role)
        return overwrite.view_channel is False, overwrite.connect is False

    def _monitor_lock(self, channel_id: int) -> asyncio.Lock:
        return self._monitor_locks.setdefault(channel_id, asyncio.Lock())

    async def _delete_monitored_message(
        self, guild: discord.Guild, announcement: Dict[str, Any]
    ) -> None:
        destination = guild.get_channel(announcement.get("destination_id"))
        message = (
            await self._fetch_message(destination, announcement.get("message_id"))
            if destination
            else None
        )
        if message:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()

    async def _clear_monitored_announcements(
        self, guild: discord.Guild, record: Dict[str, Any]
    ) -> None:
        for announcement in (record.get("announcements") or {}).values():
            await self._delete_monitored_message(guild, announcement)
        record["announcements"] = {}

    async def _restore_monitored_channels(self) -> None:
        for guild_id, data in (await self.config.all_guilds()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            monitored = data.get("monitored_channels") or {}
            changed = False
            active_channels = []
            for channel_id, record in list(monitored.items()):
                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.VoiceChannel):
                    await self._clear_monitored_announcements(guild, record)
                    monitored.pop(channel_id, None)
                    changed = True
                    continue
                if "enabled" not in record:
                    record["enabled"] = True
                    changed = True
                if "announcements" not in record:
                    record["announcements"] = {}
                    changed = True
                active_channels.append(channel)
            if changed:
                await self.config.guild(guild).monitored_channels.set(monitored)
            for channel in active_channels:
                with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                    await self._refresh_monitored_channel(channel)

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
        source_channel_id: Optional[int] = None,
        restore: bool = False,
    ) -> None:
        if not owner_id:
            return
        state = await self.get_state(channel.id)
        if not state.get("owner_id"):
            state = default_room_state(owner_id)
        state["owner_id"] = owner_id
        if source_channel_id is not None:
            state["source_channel_id"] = source_channel_id
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
        settings = await self.config.guild(channel.guild).all()
        if restore and settings.get("rsvp_enabled") and state.get("public_message_id"):
            hidden, locked = self._room_access_state(channel)
            if not hidden:
                self.bot.add_view(
                    RSVPView(self, channel.guild.id, channel.id, locked=locked),
                    message_id=state["public_message_id"],
                )
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
        game_name: str,
        settings: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[str]]:
        provider: Dict[str, Any] = {}
        diagnostics: List[str] = []
        if settings.get("igdb_enabled"):
            try:
                provider = dict(await self.providers.exact_igdb(game_name) or {})
            except MetadataProviderError as exc:
                log.info("IGDB lookup unavailable for %s: %s", game_name, exc)
                diagnostics.append(f"IGDB: {exc}")
            else:
                if provider:
                    diagnostics.append(f"IGDB: exact title match found ({provider['name']}).")
                else:
                    diagnostics.append("IGDB: no exact title or alias match found.")
        else:
            diagnostics.append("IGDB: disabled for this server.")
        if settings.get("steamgriddb_enabled") and not provider.get("image_url"):
            try:
                artwork = await self.providers.steamgriddb_art(game_name)
            except MetadataProviderError as exc:
                log.info("SteamGridDB lookup unavailable for %s: %s", game_name, exc)
                diagnostics.append(f"SteamGridDB: {exc}")
            else:
                if artwork:
                    provider["image_url"] = artwork
                    provider.setdefault("name", game_name)
                    provider.setdefault("source", "SteamGridDB")
                    diagnostics.append("SteamGridDB: safe artwork found.")
                else:
                    diagnostics.append("SteamGridDB: no exact-match safe artwork found.")
        elif not settings.get("steamgriddb_enabled"):
            diagnostics.append("SteamGridDB: disabled for this server.")
        else:
            diagnostics.append("SteamGridDB: skipped because artwork was already available.")
        return provider, diagnostics

    def _monitored_games(self, channel: discord.VoiceChannel) -> Dict[str, Dict[str, Any]]:
        games: Dict[str, Dict[str, Any]] = {}
        for member in channel.members:
            if getattr(member, "bot", False):
                continue
            activity = self._extract_activity(member)
            identity = normalize_game_name(activity.get("name"))
            if not identity:
                continue
            game = games.setdefault(
                identity,
                {
                    "identity": identity,
                    "game_name": activity["name"],
                    "player_count": 0,
                    "description": "",
                    "party": "",
                    "image_url": "",
                },
            )
            game["player_count"] += 1
            for field in ("description", "party", "image_url"):
                if not game[field] and activity.get(field):
                    game[field] = activity[field]
        return games

    def _build_monitored_embed(
        self,
        channel: discord.VoiceChannel,
        game: Dict[str, Any],
        provider: Dict[str, Any],
        locked: bool,
    ) -> discord.Embed:
        provider_matches = game_names_match(
            game.get("game_name"), [provider.get("name"), *(provider.get("aliases") or [])]
        )
        game_name = provider.get("name") if provider_matches else game["game_name"]
        provider_url = provider.get("url") if provider_matches else ""
        description = (
            first_sentence(provider.get("description")) if provider_matches else ""
        ) or game.get("description")
        image_url = game.get("image_url") or (
            provider.get("image_url") if provider_matches else ""
        )
        embed = discord.Embed(
            title=f"🎮 {game_name}",
            url=(
                provider_url
                if isinstance(provider_url, str)
                and provider_url.startswith(("https://", "http://"))
                else None
            ),
            description=(description or "")[:2000] or None,
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Players detected in game", value=str(game["player_count"]))
        member_count = sum(not getattr(member, "bot", False) for member in channel.members)
        embed.add_field(name="In Room", value=format_room_size(member_count, channel.user_limit))
        embed.add_field(name="Voice channel", value=channel.mention)
        if game.get("party"):
            embed.add_field(name="Rich Presence party", value=game["party"], inline=False)
        if locked:
            embed.add_field(
                name="Room access",
                value="🔒 Locked — connection is currently disabled.",
                inline=False,
            )
        if isinstance(image_url, str) and image_url.startswith("https://"):
            embed.set_thumbnail(url=image_url)
        embed.set_footer(text="Automatically detected in a monitored voice channel")
        return embed

    async def _save_monitored_record(
        self, guild: discord.Guild, channel_id: int, record: Dict[str, Any]
    ) -> None:
        async with self.config.guild(guild).monitored_channels() as monitored:
            if str(channel_id) in monitored:
                monitored[str(channel_id)] = record

    async def _refresh_monitored_channel(self, channel: discord.VoiceChannel) -> None:
        async with self._monitor_lock(channel.id):
            monitored = await self.config.guild(channel.guild).monitored_channels()
            record = monitored.get(str(channel.id))
            if record is None:
                return
            record.setdefault("enabled", True)
            record.setdefault("announcements", {})
            hidden, locked = self._room_access_state(channel)
            destination = channel.guild.get_channel(record.get("destination_id"))
            if (
                not record.get("enabled")
                or hidden
                or not isinstance(destination, discord.TextChannel)
            ):
                await self._clear_monitored_announcements(channel.guild, record)
                await self._save_monitored_record(channel.guild, channel.id, record)
                return

            games = self._monitored_games(channel)
            existing_announcements = record.get("announcements") or {}
            for identity, announcement in existing_announcements.items():
                if identity not in games:
                    await self._delete_monitored_message(channel.guild, announcement)

            settings = await self.config.guild(channel.guild).all()
            announcements: Dict[str, Dict[str, Any]] = {}
            for identity, game in games.items():
                previous = existing_announcements.get(identity) or {}
                if previous.get("destination_id") != destination.id:
                    await self._delete_monitored_message(channel.guild, previous)
                    previous = {}
                provider, _diagnostics = await self._resolve_provider(game["game_name"], settings)
                embed = self._build_monitored_embed(channel, game, provider, locked)
                view = MonitoredChannelView(channel.guild.id, channel.id, locked=locked)
                message = await self._fetch_message(destination, previous.get("message_id"))
                try:
                    if message:
                        await message.edit(
                            content="",
                            embed=embed,
                            view=view,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    else:
                        message = await destination.send(
                            embed=embed,
                            view=view,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(
                        "Could not publish monitored game %s for channel %s: %s",
                        game["game_name"],
                        channel.id,
                        exc,
                    )
                    message = None
                announcements[identity] = {
                    "message_id": message.id if message else None,
                    "destination_id": destination.id,
                    "game_name": game["game_name"],
                }
            record["announcements"] = announcements
            await self._save_monitored_record(channel.guild, channel.id, record)

    def _schedule_monitored_refresh(self, channel_id: int, delay: float = 30) -> None:
        old_task = self._monitor_tasks.pop(channel_id, None)
        if old_task:
            old_task.cancel()
        self._monitor_tasks[channel_id] = asyncio.create_task(
            self._refresh_monitored_after_delay(channel_id, delay)
        )

    async def _refresh_monitored_after_delay(self, channel_id: int, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            channel = self.bot.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                await self._refresh_monitored_channel(channel)
        except asyncio.CancelledError:
            return
        finally:
            if self._monitor_tasks.get(channel_id) is asyncio.current_task():
                self._monitor_tasks.pop(channel_id, None)

    async def resolve_room(self, channel: discord.VoiceChannel) -> None:
        state = await self.get_state(channel.id)
        hidden, _locked = self._room_access_state(channel)
        if not hidden and state.get("last_error") == "Hidden rooms cannot be publicly announced.":
            state["last_error"] = None
        preset_name, preset, presets = await self._preset_for_state(channel, state)
        detected = state.get("detected") or {}
        settings = await self.config.guild(channel.guild).all()

        manual = state.get("manual_overrides") or {}
        if state.get("source_choice") == "manual":
            initial_game = (
                manual.get("game_name") or preset_game_name(preset) or detected.get("name")
            )
        else:
            initial_game = (
                preset_game_name(preset) or detected.get("name") or manual.get("game_name")
            )
        provider = state.get("provider") or {}
        if initial_game and not game_names_match(provider.get("name"), [initial_game]):
            provider, diagnostics = await self._resolve_provider(initial_game, settings)
            state["provider_diagnostics"] = diagnostics
        elif not initial_game:
            provider = {}
            state["provider_diagnostics"] = []
        state["provider"] = provider

        mapped_role_id = state.get("selected_role_id")
        if not mapped_role_id and detected.get("name") and not preset:
            for candidate in presets.values():
                if game_names_match(
                    detected["name"],
                    [preset_game_name(candidate), *(candidate.get("game_aliases") or [])],
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
        had_public_announcement = bool(
            state.get("public_message_id") or state.get("hidden_public_suspended")
        )
        new_identity = resolved.get("identity")
        if old_identity and new_identity != old_identity:
            await self._delete_public(channel, state, suppress=False)
            state["suppressed_identity"] = None
            state["missing_role_warned_identity"] = None
            state["tagged_identity"] = None
        if state.get("rsvp_identity") != new_identity:
            state["rsvp_identity"] = new_identity
            state["rsvp_responses"] = {}
            state["rsvp_milestones"] = []
        state["identity"] = new_identity
        state["resolved"] = resolved
        state["auto_eligible"] = automatic_metadata_ready(resolved, preset, detected, provider)
        await self._save_state(channel.id, state)
        await self.ensure_preview(channel)
        await self.refresh_control(channel)

        if hidden:
            if state.get("public_message_id"):
                state["hidden_public_suspended"] = True
                await self._delete_public(channel, state, suppress=False)
            return

        should_publish = (had_public_announcement or settings.get("auto_announce")) and (
            state.get("announcements_enabled")
            and (had_public_announcement or state.get("auto_eligible"))
            and new_identity
            and state.get("suppressed_identity") != new_identity
        )
        if should_publish:
            try:
                await self.publish_room(
                    channel,
                    tag_source=(
                        "automatic"
                        if is_new_game_post(had_public_announcement, old_identity, new_identity)
                        else "none"
                    ),
                )
            except (RuntimeError, discord.Forbidden, discord.HTTPException) as exc:
                state = await self.get_state(channel.id)
                state["last_error"] = str(exc)
                await self._save_state(channel.id, state)
                await self.ensure_preview(channel)

    def _preview_should_be_visible(self, state: Dict[str, Any]) -> bool:
        return preview_should_be_visible(state)

    def _rsvp_field_value(
        self, guild: discord.Guild, user_ids: List[int], show_names: bool
    ) -> str:
        if not show_names or not user_ids:
            return str(len(user_ids))
        names = []
        for user_id in user_ids:
            member = guild.get_member(user_id)
            if member is None:
                continue
            name = discord.utils.escape_mentions(
                discord.utils.escape_markdown(member.display_name)
            )
            names.append(name)
            if len(names) == 5:
                break
        value = f"**{len(user_ids)}**"
        if names:
            value += "\n" + ", ".join(names)
            if len(user_ids) > len(names):
                value += f" +{len(user_ids) - len(names)} more"
        return value[:1024]

    def _build_embed(
        self,
        channel: discord.VoiceChannel,
        state: Dict[str, Any],
        public: bool = False,
        guild_settings: Optional[Dict[str, Any]] = None,
    ) -> discord.Embed:
        resolved = state.get("resolved") or {}
        game_name = resolved.get("game_name") or MISSING_GAME
        title = f"🎮 {game_name}" if public else "🎮 Room Announcement Preview"
        provider_url = resolved.get("provider_url") or ""
        embed = discord.Embed(
            title=title,
            url=provider_url if provider_url.startswith(("https://", "http://")) else None,
            color=discord.Color.blurple(),
        )
        if public:
            if resolved.get("description"):
                embed.description = resolved["description"][:2000]
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
        if resolved.get("party") or not public:
            embed.add_field(name="Game party", value=resolved.get("party") or MISSING_PARTY)
        embed.add_field(
            name="In Room", value=format_room_size(len(channel.members), channel.user_limit)
        )
        embed.add_field(name="Voice channel", value=channel.mention)
        if resolved.get("note"):
            embed.add_field(name="Note", value=resolved["note"][:1024], inline=False)
        if not public:
            hidden, locked = self._room_access_state(channel)
            if hidden:
                status = "Hidden — public publishing is suppressed"
            elif locked and state.get("public_message_id"):
                status = "Published — room locked"
            elif locked:
                status = "Ready — room locked; tagging is suppressed"
            elif state.get("last_error"):
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
            diagnostics = state.get("provider_diagnostics") or []
            if diagnostics:
                embed.add_field(
                    name="Metadata providers",
                    value="\n".join(f"• {item}" for item in diagnostics)[:1024],
                    inline=False,
                )
        if public and (guild_settings or {}).get("rsvp_enabled"):
            groups = rsvp_groups(state.get("rsvp_responses") or {})
            show_names = bool(guild_settings.get("rsvp_show_names"))
            inline_fields = 0
            for field in reversed(embed.fields):
                if not field.inline:
                    break
                inline_fields += 1
            for _ in range((-inline_fields) % 3):
                embed.add_field(name="\u200b", value="\u200b")
            embed.add_field(
                name="Joining",
                value=self._rsvp_field_value(channel.guild, groups["join"], show_names),
            )
            embed.add_field(
                name="Maybe",
                value=self._rsvp_field_value(channel.guild, groups["maybe"], show_names),
            )
            embed.add_field(
                name="Not Coming",
                value=self._rsvp_field_value(channel.guild, groups["not_coming"], show_names),
            )
        if public and self._room_access_state(channel)[1]:
            embed.add_field(
                name="Room access",
                value="🔒 Locked — connection and new role pings are currently disabled.",
                inline=False,
            )
        if resolved.get("image_url", "").startswith("https://"):
            embed.set_thumbnail(url=resolved["image_url"])
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
        self, channel: discord.VoiceChannel, tag_source: str = "none"
    ) -> discord.Message:
        state = await self.get_state(channel.id)
        if not state.get("announcements_enabled"):
            raise RuntimeError("Announcements are disabled for this room.")
        hidden, locked = self._room_access_state(channel)
        if hidden:
            if state.get("public_message_id"):
                state["hidden_public_suspended"] = True
                await self._delete_public(channel, state, suppress=False)
            raise RuntimeError("Hidden rooms cannot be publicly announced.")
        if not has_game_context(state.get("resolved") or {}):
            raise RuntimeError("No game is configured yet.")
        guild_settings = await self.config.guild(channel.guild).all()
        destination_id = announcement_destination_id(state, guild_settings)
        destination = channel.guild.get_channel(destination_id) if destination_id else None
        if not isinstance(destination, discord.TextChannel):
            source_id = state.get("source_channel_id")
            source = channel.guild.get_channel(source_id) if source_id else None
            source_name = source.mention if source else "this room's Join-to-Create channel"
            raise RuntimeError(
                f"No announcement channel is configured for {source_name}, and no default exists."
            )

        role_id = (state.get("resolved") or {}).get("role_id")
        role = channel.guild.get_role(role_id) if role_id else None
        tag_requested = tag_source in {"automatic", "manual"}
        tag_allowed = tagging_is_allowed(guild_settings, tag_source) and not locked
        should_tag = bool(
            tag_requested
            and tag_allowed
            and role
            and state.get("tagged_identity") != state.get("identity")
        )
        if tag_requested and tag_allowed and role is None:
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

        embed = self._build_embed(channel, state, public=True, guild_settings=guild_settings)
        view = (
            RSVPView(self, channel.guild.id, channel.id, locked=locked)
            if guild_settings.get("rsvp_enabled")
            else None
        )
        if existing:
            await existing.edit(
                content="",
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
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
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=allowed_mentions,
            )
        state["public_message_id"] = message.id
        state["public_channel_id"] = destination.id
        state["hidden_public_suspended"] = False
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
            state["hidden_public_suspended"] = False
            state["rsvp_responses"] = {}
            state["rsvp_milestones"] = []
        await self._save_state(channel.id, state)

    async def cleanup_room(self, channel: discord.VoiceChannel) -> None:
        state = await self.get_state(channel.id)
        await self._delete_public(channel, state, suppress=False)
        preview = await self._fetch_message(channel, state.get("preview_message_id"))
        if preview:
            with contextlib.suppress(discord.HTTPException):
                await preview.delete()
        self._rsvp_locks.pop(channel.id, None)
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
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(channel_id)
        options = await self._game_choice_options(interaction.guild, channel_id)
        await interaction.followup.send(
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
            if roomer:
                try:
                    await roomer.apply_room_preset(channel, preset_name)
                except (ValueError, discord.HTTPException) as exc:
                    return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            else:
                state["selected_preset"] = preset_name
                state["source_choice"] = "preset"
                state.setdefault("manual_overrides", {}).pop("game_name", None)
                await self._save_state(channel_id, state)
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
        await interaction.response.defer(ephemeral=True)
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
        settings = await self.config.guild(interaction.guild).all()
        description = (
            "Choose an approved role. Selecting one also republishes the current announcement with a tag."
            if tagging_is_allowed(settings, "manual")
            else "Choose an approved role. Forced active hours currently prevent the role from being tagged."
        )
        await interaction.followup.send(
            description,
            view=RoleChoiceView(self, channel_id, roles),
            ephemeral=True,
        )

    def _rsvp_lock(self, channel_id: int) -> asyncio.Lock:
        return self._rsvp_locks.setdefault(channel_id, asyncio.Lock())

    async def _rsvp_context(
        self, interaction: discord.Interaction, channel_id: int
    ) -> tuple[Optional[discord.VoiceChannel], Optional[Dict[str, Any]], Optional[str]]:
        if interaction.guild is None:
            return None, None, "RSVP controls can only be used in a server."
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return None, None, "This room no longer exists."
        state = await self.get_state(channel_id)
        settings = await self.config.guild(interaction.guild).all()
        if not settings.get("rsvp_enabled"):
            return channel, state, "RSVP controls are disabled on this server."
        message_id = getattr(getattr(interaction, "message", None), "id", None)
        if not message_id or message_id != state.get("public_message_id"):
            return channel, state, "This announcement is no longer current."
        if not state.get("identity") or state.get("rsvp_identity") != state.get("identity"):
            return channel, state, "This announcement's game session is no longer current."
        return channel, state, None

    async def _notify_rsvp_milestone(
        self, channel: discord.VoiceChannel, state: Dict[str, Any], milestone: int
    ) -> None:
        owner = channel.guild.get_member(state.get("owner_id"))
        if owner is None:
            return
        noun = "person plans" if milestone == 1 else "people plan"
        try:
            await channel.send(
                f"🎉 {owner.mention}, {milestone} {noun} to join this session.",
                allowed_mentions=discord.AllowedMentions(
                    users=[owner], roles=False, everyone=False, replied_user=False
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Failed to send RSVP milestone %s for room %s", milestone, channel.id)

    async def set_rsvp(
        self, interaction: discord.Interaction, channel_id: int, status: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if status not in RSVP_STATUSES:
            return await interaction.followup.send("Invalid RSVP response.", ephemeral=True)
        milestone = None
        async with self._rsvp_lock(channel_id):
            channel, state, error = await self._rsvp_context(interaction, channel_id)
            if error:
                return await interaction.followup.send(f"❌ {error}", ephemeral=True)
            responses = state.get("rsvp_responses") or {}
            responses[str(interaction.user.id)] = status
            state["rsvp_responses"] = responses
            join_count = len(rsvp_groups(responses)["join"])
            milestone, consumed = consume_rsvp_milestones(
                state.get("rsvp_milestones") or [], join_count
            )
            state["rsvp_milestones"] = consumed
            await self._save_state(channel_id, state)
            try:
                await self.publish_room(channel)
            except (RuntimeError, discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not refresh RSVP announcement for room %s: %s", channel_id, exc)
            if milestone is not None:
                await self._notify_rsvp_milestone(channel, state, milestone)
        labels = {"join": "Join", "maybe": "Maybe", "not_coming": "Not Coming"}
        await interaction.followup.send(
            f"✅ Your response is now **{labels[status]}**.", ephemeral=True
        )

    def _participant_pages(self, guild: discord.Guild, responses: Dict[str, str]) -> List[str]:
        groups = rsvp_groups(responses)
        labels = {"join": "Joining", "maybe": "Maybe", "not_coming": "Not Coming"}
        lines = []
        for status in RSVP_STATUSES:
            user_ids = groups[status]
            lines.append(f"**{labels[status]} ({len(user_ids)})**")
            if not user_ids:
                lines.append("• None")
                continue
            for user_id in user_ids:
                member = guild.get_member(user_id)
                name = member.display_name if member else f"Unknown member ({user_id})"
                name = discord.utils.escape_mentions(discord.utils.escape_markdown(name))
                lines.append(f"• {name}")
        pages = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 3500:
                pages.append(current)
                current = line
            else:
                current = candidate
        if current or not pages:
            pages.append(current or "No responses yet.")
        return pages

    async def show_participants(self, interaction: discord.Interaction, channel_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        channel, state, error = await self._rsvp_context(interaction, channel_id)
        if error:
            return await interaction.followup.send(f"❌ {error}", ephemeral=True)
        for index, page in enumerate(
            self._participant_pages(interaction.guild, state.get("rsvp_responses") or {})
        ):
            embed = discord.Embed(
                title=(
                    "Announcement Participants"
                    if index == 0
                    else f"Announcement Participants — Page {index + 1}"
                ),
                description=page,
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

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
            settings = await self.config.guild(interaction.guild).all()
            _hidden, locked = self._room_access_state(channel)
            manual_tag_allowed = tagging_is_allowed(settings, "manual") and not locked
            try:
                await self.publish_room(channel, tag_source="manual")
            except RuntimeError as exc:
                return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            if locked:
                return await interaction.followup.send(
                    "✅ Announcement role updated, but locked rooms cannot send new role pings.",
                    ephemeral=True,
                )
            if not manual_tag_allowed:
                return await interaction.followup.send(
                    "✅ Announcement role updated, but forced active hours currently prevent role tagging.",
                    ephemeral=True,
                )
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
        state["provider"] = {}
        state["provider_diagnostics"] = []
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
            state["hidden_public_suspended"] = False
            state["rsvp_responses"] = {}
            state["rsvp_milestones"] = []
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
        roomer = self._roomer()
        room = await roomer.get_room(channel.id) if roomer else {}
        await self.ensure_room(
            channel,
            owner_id,
            source_channel_id=(room or {}).get("source_channel_id"),
        )

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
        if state.get("selected_preset") == preset_name:
            return
        await self.sync_room_preset(channel, preset_name, preset)

    async def sync_room_preset(
        self,
        channel: discord.VoiceChannel,
        preset_name: Optional[str],
        preset: Dict[str, Any],
    ) -> None:
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

    async def _handle_room_access_change(self, channel: discord.VoiceChannel) -> None:
        hidden, _locked = self._room_access_state(channel)
        state = await self.get_state(channel.id)
        if hidden:
            if state.get("public_message_id"):
                state["hidden_public_suspended"] = True
                await self._delete_public(channel, state, suppress=False)
            await self.ensure_preview(channel)
            await self.refresh_control(channel)
            return
        await self.resolve_room(channel)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        if not isinstance(after, discord.VoiceChannel):
            return
        access_changed = self._room_access_state(before) != self._room_access_state(after)
        roomer = self._roomer()
        if access_changed and roomer is not None and after.id in roomer.channel_owners:
            await self._handle_room_access_change(after)
        monitored = await self.config.guild(after.guild).monitored_channels()
        if str(after.id) in monitored:
            await self._refresh_monitored_channel(after)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        monitored = await self.config.guild(channel.guild).monitored_channels()
        record = monitored.get(str(channel.id))
        if record is not None:
            async with self._monitor_lock(channel.id):
                await self._clear_monitored_announcements(channel.guild, record)
                monitored.pop(str(channel.id), None)
                await self.config.guild(channel.guild).monitored_channels.set(monitored)
            task = self._monitor_tasks.pop(channel.id, None)
            if task:
                task.cancel()
            self._monitor_locks.pop(channel.id, None)
        for current in monitored.values():
            if current.get("destination_id") == channel.id:
                current["announcements"] = {}
        await self.config.guild(channel.guild).monitored_channels.set(monitored)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        roomer = self._roomer()
        if roomer is not None:
            channel_id = next(
                (
                    room_id
                    for room_id, owner_id in roomer.channel_owners.items()
                    if owner_id == after.id
                ),
                None,
            )
            if channel_id is not None:
                old_task = self._presence_tasks.pop(channel_id, None)
                if old_task:
                    old_task.cancel()
                self._presence_tasks[channel_id] = asyncio.create_task(
                    self._apply_presence_after_delay(channel_id, after)
                )
        voice_channel = getattr(getattr(after, "voice", None), "channel", None)
        if isinstance(voice_channel, discord.VoiceChannel):
            monitored = await self.config.guild(after.guild).monitored_channels()
            record = monitored.get(str(voice_channel.id))
            if record and record.get("enabled", True):
                self._schedule_monitored_refresh(voice_channel.id)

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens):
        if service_name in {"twitch", "steamgriddb"}:
            self.providers.invalidate(service_name)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        user_key = str(member.id)
        for channel in self._active_guild_rooms(member.guild):
            async with self._rsvp_lock(channel.id):
                state = await self.get_state(channel.id)
                responses = state.get("rsvp_responses") or {}
                if user_key not in responses:
                    continue
                responses.pop(user_key, None)
                state["rsvp_responses"] = responses
                await self._save_state(channel.id, state)
                if state.get("public_message_id"):
                    with contextlib.suppress(
                        RuntimeError, discord.Forbidden, discord.HTTPException
                    ):
                        await self.publish_room(channel)

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
        if roomer is not None:
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
        monitored = await self.config.guild(member.guild).monitored_channels()
        monitored_ids = {
            channel.id
            for channel in (before.channel, after.channel)
            if channel
            and (record := monitored.get(str(channel.id)))
            and record.get("enabled", True)
        }
        for channel_id in monitored_ids:
            self._schedule_monitored_refresh(channel_id)

    @monitor_group.command(
        name="add", description="Monitor a static voice channel for informational game posts."
    )
    @app_commands.default_permissions(administrator=True)
    async def monitor_add(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
        announcement_channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)
        roomer = self._roomer()
        if roomer is not None and voice_channel.id in roomer.channel_owners:
            return await interaction.followup.send(
                "❌ Temporary Roomer channels already use the full announcement workflow.",
                ephemeral=True,
            )
        source_permissions = voice_channel.permissions_for(interaction.guild.me)
        if not source_permissions.view_channel:
            return await interaction.followup.send(
                "❌ The bot must be able to view the monitored voice channel.", ephemeral=True
            )
        destination_permissions = announcement_channel.permissions_for(interaction.guild.me)
        if not destination_permissions.send_messages or not destination_permissions.embed_links:
            return await interaction.followup.send(
                "❌ The bot needs Send Messages and Embed Links in the announcement channel.",
                ephemeral=True,
            )
        async with self._monitor_lock(voice_channel.id):
            monitored = await self.config.guild(interaction.guild).monitored_channels()
            previous = monitored.get(str(voice_channel.id))
            if previous:
                await self._clear_monitored_announcements(interaction.guild, previous)
            monitored[str(voice_channel.id)] = {
                "destination_id": announcement_channel.id,
                "enabled": True,
                "announcements": {},
            }
            await self.config.guild(interaction.guild).monitored_channels.set(monitored)
        await self._refresh_monitored_channel(voice_channel)
        await interaction.followup.send(
            f"✅ Monitoring {voice_channel.mention}; informational game posts will be sent to "
            f"{announcement_channel.mention}.",
            ephemeral=True,
        )

    @monitor_group.command(name="remove", description="Stop monitoring a static voice channel.")
    @app_commands.default_permissions(administrator=True)
    async def monitor_remove(
        self, interaction: discord.Interaction, voice_channel: discord.VoiceChannel
    ):
        await interaction.response.defer(ephemeral=True)
        async with self._monitor_lock(voice_channel.id):
            monitored = await self.config.guild(interaction.guild).monitored_channels()
            record = monitored.get(str(voice_channel.id))
            if record is None:
                return await interaction.followup.send(
                    "❌ That voice channel is not monitored.", ephemeral=True
                )
            await self._clear_monitored_announcements(interaction.guild, record)
            monitored.pop(str(voice_channel.id), None)
            await self.config.guild(interaction.guild).monitored_channels.set(monitored)
        task = self._monitor_tasks.pop(voice_channel.id, None)
        if task:
            task.cancel()
        self._monitor_locks.pop(voice_channel.id, None)
        await interaction.followup.send(
            f"✅ Stopped monitoring {voice_channel.mention} and removed its informational posts.",
            ephemeral=True,
        )

    @monitor_group.command(name="list", description="List monitored static voice channels.")
    @app_commands.default_permissions(administrator=True)
    async def monitor_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        monitored = await self.config.guild(interaction.guild).monitored_channels()
        lines = []
        for channel_id, record in monitored.items():
            voice = interaction.guild.get_channel(int(channel_id))
            destination = interaction.guild.get_channel(record.get("destination_id"))
            voice_label = voice.mention if voice else f"Deleted voice channel (`{channel_id}`)"
            destination_label = (
                destination.mention
                if destination
                else f"Deleted destination (`{record.get('destination_id')}`)"
            )
            status = "Enabled" if record.get("enabled", True) else "Disabled"
            active_posts = sum(
                bool(item.get("message_id"))
                for item in (record.get("announcements") or {}).values()
            )
            lines.append(
                f"• {voice_label} → {destination_label} — **{status}**, "
                f"{active_posts} active post(s)"
            )
        pages = [lines[index : index + 15] for index in range(0, len(lines), 15)] or [[]]
        for index, page in enumerate(pages):
            embed = discord.Embed(
                title=(
                    "Monitored Voice Channels"
                    if index == 0
                    else f"Monitored Voice Channels — Page {index + 1}"
                ),
                description="\n".join(page) or "No static voice channels are monitored.",
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @monitor_group.command(
        name="enable", description="Enable or disable a monitored static voice channel."
    )
    @app_commands.default_permissions(administrator=True)
    async def monitor_enable(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
        enabled: bool,
    ):
        await interaction.response.defer(ephemeral=True)
        async with self._monitor_lock(voice_channel.id):
            monitored = await self.config.guild(interaction.guild).monitored_channels()
            record = monitored.get(str(voice_channel.id))
            if record is None:
                return await interaction.followup.send(
                    "❌ That voice channel is not monitored.", ephemeral=True
                )
            record["enabled"] = enabled
            if not enabled:
                await self._clear_monitored_announcements(interaction.guild, record)
            monitored[str(voice_channel.id)] = record
            await self.config.guild(interaction.guild).monitored_channels.set(monitored)
        if enabled:
            await self._refresh_monitored_channel(voice_channel)
        await interaction.followup.send(
            f"✅ Monitoring for {voice_channel.mention} is now "
            f"{'enabled' if enabled else 'disabled'}.",
            ephemeral=True,
        )

    @roomannounce_group.command(
        name="channel", description="Set a default or Join-to-Create announcement channel."
    )
    @app_commands.describe(
        channel="Announcement destination; omit to clear the selected mapping",
        join_to_create="Join-to-Create source; omit to configure the default destination",
    )
    @app_commands.default_permissions(administrator=True)
    async def channel_command(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        join_to_create: Optional[discord.VoiceChannel] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        roomer = self._roomer()
        if join_to_create is not None:
            configured_sources = (
                await roomer.config.guild(interaction.guild).auto_channels() if roomer else []
            )
            if join_to_create.id not in configured_sources:
                return await interaction.followup.send(
                    "❌ That voice channel is not configured as a Roomer Join-to-Create channel.",
                    ephemeral=True,
                )
        rooms_to_republish = []
        for room in self._active_guild_rooms(interaction.guild):
            state = await self.get_state(room.id)
            if state.get("public_message_id"):
                rooms_to_republish.append(room)
                await self._delete_public(room, state, suppress=False)
        if join_to_create is None:
            await self.config.guild(interaction.guild).announcement_channel_id.set(
                channel.id if channel else None
            )
            target = "Default announcement channel"
        else:
            async with self.config.guild(interaction.guild).announcement_channels() as mappings:
                if channel:
                    mappings[str(join_to_create.id)] = channel.id
                else:
                    mappings.pop(str(join_to_create.id), None)
            target = f"Announcement channel for {join_to_create.mention}"
        for room in rooms_to_republish:
            with contextlib.suppress(RuntimeError, discord.HTTPException):
                await self.publish_room(room)
        await interaction.followup.send(
            f"✅ {target} {'set to ' + channel.mention if channel else 'cleared'}.",
            ephemeral=True,
        )

    @roomannounce_group.command(
        name="channels", description="List announcement destinations by Join-to-Create channel."
    )
    @app_commands.default_permissions(administrator=True)
    async def list_channels(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await self.config.guild(interaction.guild).all()
        default = interaction.guild.get_channel(data.get("announcement_channel_id"))
        roomer = self._roomer()
        configured_sources = (
            await roomer.config.guild(interaction.guild).auto_channels() if roomer else []
        )
        mappings = data.get("announcement_channels") or {}
        mapped_source_ids = []
        for key in mappings:
            with contextlib.suppress(TypeError, ValueError):
                mapped_source_ids.append(int(key))
        source_ids = list(dict.fromkeys([*configured_sources, *mapped_source_ids]))
        configured_source_ids = set(configured_sources)
        lines = []
        for source_id in source_ids:
            source = interaction.guild.get_channel(source_id)
            destination = interaction.guild.get_channel(mappings.get(str(source_id)))
            source_label = source.mention if source else f"Deleted source (`{source_id}`)"
            if source_id not in configured_source_ids:
                source_label += " *(not configured in Roomer)*"
            if destination:
                destination_label = destination.mention
            elif str(source_id) in mappings:
                destination_label = "Deleted destination"
            elif default:
                destination_label = f"{default.mention} *(default)*"
            else:
                destination_label = "Not configured"
            lines.append(f"• {source_label} → {destination_label}")
        pages = [lines[index : index + 20] for index in range(0, len(lines), 20)] or [[]]
        for index, page in enumerate(pages):
            default_line = (
                f"Default: {default.mention if default else 'Not configured'}\n\n"
                if index == 0
                else ""
            )
            embed = discord.Embed(
                title=(
                    "RoomAnnounce Channel Destinations"
                    if index == 0
                    else f"RoomAnnounce Channel Destinations — Page {index + 1}"
                ),
                description=default_line
                + ("\n".join(page) or "No Join-to-Create channels are configured."),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @roomannounce_group.command(
        name="autoannounce", description="Toggle automatic publishing for Roomer-created rooms."
    )
    @app_commands.default_permissions(administrator=True)
    async def autoannounce(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.config.guild(interaction.guild).auto_announce.set(enabled)
        if enabled:
            for room in self._active_guild_rooms(interaction.guild):
                await self.resolve_room(room)
        await interaction.followup.send(
            f"✅ Automatic Roomer announcements {'enabled' if enabled else 'disabled'}.",
            ephemeral=True,
        )

    @roomannounce_group.command(name="autotag", description="Toggle automatic role tagging.")
    @app_commands.default_permissions(administrator=True)
    async def autotag(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.config.guild(interaction.guild).auto_tag.set(enabled)
        await interaction.followup.send(
            f"✅ Automatic role tagging {'enabled' if enabled else 'disabled'}. "
            "This affects future initial or new-game posts and never re-pings existing announcements.",
            ephemeral=True,
        )

    @activehours_group.command(name="set", description="Set and enable active tagging hours.")
    @app_commands.describe(
        timezone="IANA timezone, for example Europe/Vilnius",
        start="Start time in 24-hour HH:MM format",
        end="End time in 24-hour HH:MM format",
        weekdays="Optional comma-separated weekdays; defaults to every day",
    )
    @app_commands.default_permissions(administrator=True)
    async def activehours_set(
        self,
        interaction: discord.Interaction,
        timezone: str,
        start: str,
        end: str,
        weekdays: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            timezone = validate_timezone(timezone)
            start_minutes = parse_clock(start)
            end_minutes = parse_clock(end)
            days = parse_weekdays(weekdays)
            if start_minutes == end_minutes:
                raise ValueError("Start and end times must be different.")
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        start = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
        end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
        group = self.config.guild(interaction.guild)
        await group.active_hours_timezone.set(timezone)
        await group.active_hours_start.set(start)
        await group.active_hours_end.set(end)
        await group.active_hours_weekdays.set(days)
        await group.active_hours_enabled.set(True)
        await interaction.followup.send(
            f"✅ Active hours enabled: **{active_hours_days_text(days)}**, "
            f"**{start}–{end}** in **{timezone}**.",
            ephemeral=True,
        )

    @activehours_set.autocomplete("timezone")
    async def activehours_timezone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        current = current.casefold().strip()
        matches = [item for item in self._timezones if current in item.casefold()]
        return [app_commands.Choice(name=item, value=item) for item in matches[:25]]

    @activehours_group.command(name="enable", description="Enable or disable active hours.")
    @app_commands.default_permissions(administrator=True)
    async def activehours_enable(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        group = self.config.guild(interaction.guild)
        if enabled:
            data = await group.all()
            try:
                validate_timezone(data.get("active_hours_timezone"))
                start = parse_clock(data.get("active_hours_start"))
                end = parse_clock(data.get("active_hours_end"))
                if start == end:
                    raise ValueError("Start and end times must be different.")
            except ValueError as exc:
                return await interaction.followup.send(
                    f"❌ Configure valid active hours first: {exc}", ephemeral=True
                )
        await group.active_hours_enabled.set(enabled)
        if not enabled:
            await group.active_hours_forced.set(False)
        await interaction.followup.send(
            f"✅ Active hours {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @activehours_group.command(
        name="force", description="Forbid manual role tags outside active hours."
    )
    @app_commands.default_permissions(administrator=True)
    async def activehours_force(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        group = self.config.guild(interaction.guild)
        if enabled and not await group.active_hours_enabled():
            return await interaction.followup.send(
                "❌ Enable and configure active hours before forcing them.", ephemeral=True
            )
        await group.active_hours_forced.set(enabled)
        await interaction.followup.send(
            f"✅ Forced active hours {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @activehours_group.command(name="clear", description="Clear the active-hours schedule.")
    @app_commands.default_permissions(administrator=True)
    async def activehours_clear(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        group = self.config.guild(interaction.guild)
        await group.active_hours_enabled.set(False)
        await group.active_hours_forced.set(False)
        await group.active_hours_timezone.set("")
        await group.active_hours_start.set("")
        await group.active_hours_end.set("")
        await group.active_hours_weekdays.set(list(range(7)))
        await interaction.followup.send("✅ Active hours cleared.", ephemeral=True)

    @activehours_group.command(name="settings", description="Show active-hours settings.")
    @app_commands.default_permissions(administrator=True)
    async def activehours_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await self.config.guild(interaction.guild).all()
        enabled = bool(data.get("active_hours_enabled"))
        active = active_hours_are_active(data) if enabled else False
        embed = discord.Embed(title="RoomAnnounce Active Hours", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value=str(enabled))
        embed.add_field(name="Currently active", value=str(active) if enabled else "Disabled")
        embed.add_field(name="Forced", value=str(data.get("active_hours_forced", False)))
        embed.add_field(
            name="Timezone", value=data.get("active_hours_timezone") or "Not configured"
        )
        embed.add_field(
            name="Window",
            value=(
                f"{data.get('active_hours_start')}–{data.get('active_hours_end')}"
                if data.get("active_hours_start") and data.get("active_hours_end")
                else "Not configured"
            ),
        )
        embed.add_field(
            name="Weekdays",
            value=active_hours_days_text(data.get("active_hours_weekdays") or []),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _refresh_rsvp_configuration(
        self, guild: discord.Guild, clear_responses: bool = False
    ) -> None:
        for room in self._active_guild_rooms(guild):
            state = await self.get_state(room.id)
            if clear_responses or state.get("rsvp_identity") != state.get("identity"):
                state["rsvp_identity"] = state.get("identity")
                state["rsvp_responses"] = {}
                state["rsvp_milestones"] = []
                await self._save_state(room.id, state)
            if state.get("public_message_id"):
                with contextlib.suppress(RuntimeError, discord.Forbidden, discord.HTTPException):
                    await self.publish_room(room)

    @rsvp_group.command(name="enable", description="Enable or disable announcement RSVP.")
    @app_commands.default_permissions(administrator=True)
    async def rsvp_enable(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.config.guild(interaction.guild).rsvp_enabled.set(enabled)
        await self._refresh_rsvp_configuration(interaction.guild, clear_responses=not enabled)
        await interaction.followup.send(
            f"✅ Announcement RSVP {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @rsvp_group.command(name="names", description="Show or hide RSVP participant names.")
    @app_commands.default_permissions(administrator=True)
    async def rsvp_names(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.config.guild(interaction.guild).rsvp_show_names.set(enabled)
        await self._refresh_rsvp_configuration(interaction.guild)
        await interaction.followup.send(
            f"✅ RSVP participant names {'shown' if enabled else 'hidden'}.", ephemeral=True
        )

    @rsvp_group.command(name="settings", description="Show RSVP settings.")
    @app_commands.default_permissions(administrator=True)
    async def rsvp_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await self.config.guild(interaction.guild).all()
        embed = discord.Embed(title="RoomAnnounce RSVP", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value=str(data.get("rsvp_enabled", False)))
        embed.add_field(
            name="Display participant names", value=str(data.get("rsvp_show_names", False))
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        self.providers.invalidate("twitch" if provider.value == "igdb" else provider.value)
        for room in self._active_guild_rooms(interaction.guild):
            state = await self.get_state(room.id)
            state["provider"] = {}
            await self._save_state(room.id, state)
            await self.resolve_room(room)
        monitored = await self.config.guild(interaction.guild).monitored_channels()
        for channel_id in monitored:
            channel = interaction.guild.get_channel(int(channel_id))
            if isinstance(channel, discord.VoiceChannel):
                await self._refresh_monitored_channel(channel)
        await interaction.followup.send(
            f"✅ {provider.name} {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @roomannounce_group.command(
        name="lookup", description="Test metadata providers for an exact game title."
    )
    @app_commands.default_permissions(administrator=True)
    async def lookup(self, interaction: discord.Interaction, game_name: str):
        await interaction.response.defer(ephemeral=True)
        settings = await self.config.guild(interaction.guild).all()
        provider, diagnostics = await self._resolve_provider(game_name.strip(), settings)
        embed = discord.Embed(
            title=f"Metadata lookup: {game_name.strip()[:100]}",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Diagnostics",
            value="\n".join(f"• {item}" for item in diagnostics)[:1024] or "No providers ran.",
            inline=False,
        )
        if provider:
            embed.add_field(name="Matched title", value=provider.get("name") or "Unknown")
            embed.add_field(
                name="Description", value="Available" if provider.get("description") else "Missing"
            )
            embed.add_field(
                name="Artwork", value="Available" if provider.get("image_url") else "Missing"
            )
            if provider.get("url"):
                embed.add_field(name="Source", value=provider["url"], inline=False)
        else:
            embed.add_field(name="Result", value="No usable metadata found.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        role_ids = await self.config.guild(interaction.guild).allowed_role_ids()
        if action.value == "list":
            roles = [interaction.guild.get_role(role_id) for role_id in role_ids]
            value = ", ".join(item.mention for item in roles if item) or "None"
            return await interaction.followup.send(
                f"Approved announcement roles: {value}", ephemeral=True
            )
        if role is None:
            return await interaction.followup.send(
                "❌ Select a role for this action.", ephemeral=True
            )
        if action.value == "add":
            if role.id not in role_ids:
                if len(role_ids) >= 24:
                    return await interaction.followup.send(
                        "❌ At most 24 approved roles are supported so the selector can include a clear option.",
                        ephemeral=True,
                    )
                role_ids.append(role.id)
        elif role.id in role_ids:
            role_ids.remove(role.id)
        await self.config.guild(interaction.guild).allowed_role_ids.set(role_ids)
        await interaction.followup.send(
            f"✅ {role.mention} {'added to' if action.value == 'add' else 'removed from'} approved roles.",
            ephemeral=True,
        )

    @roomannounce_group.command(name="settings", description="Show announcement settings.")
    @app_commands.default_permissions(administrator=True)
    async def settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await self.config.guild(interaction.guild).all()
        twitch_tokens = await self.bot.get_shared_api_tokens("twitch")
        steamgriddb_tokens = await self.bot.get_shared_api_tokens("steamgriddb")
        channel = interaction.guild.get_channel(data["announcement_channel_id"])
        roles = [interaction.guild.get_role(role_id) for role_id in data["allowed_role_ids"]]
        embed = discord.Embed(title="RoomAnnounce Settings", color=discord.Color.blurple())
        embed.add_field(
            name="Default channel", value=channel.mention if channel else "Not configured"
        )
        embed.add_field(
            name="Join-to-Create mappings",
            value=str(len(data.get("announcement_channels") or {})),
        )
        embed.add_field(
            name="Monitored static channels",
            value=str(len(data.get("monitored_channels") or {})),
        )
        embed.add_field(name="Automatic Roomer announce", value=str(data["auto_announce"]))
        embed.add_field(name="Automatic tag", value=str(data["auto_tag"]))
        schedule_enabled = bool(data.get("active_hours_enabled"))
        schedule_active = active_hours_are_active(data) if schedule_enabled else True
        embed.add_field(
            name="Autotag effective now",
            value=str(bool(data["auto_tag"] and schedule_active)),
        )
        embed.add_field(
            name="Active hours",
            value=(
                f"{data.get('active_hours_start')}–{data.get('active_hours_end')} "
                f"({data.get('active_hours_timezone')}); "
                f"{'active' if schedule_active else 'inactive'}"
                if schedule_enabled
                else "Disabled"
            ),
            inline=False,
        )
        embed.add_field(
            name="Forced active hours", value=str(data.get("active_hours_forced", False))
        )
        embed.add_field(name="RSVP", value=str(data.get("rsvp_enabled", False)))
        embed.add_field(
            name="RSVP participant names", value=str(data.get("rsvp_show_names", False))
        )
        igdb_credentials = bool(
            twitch_tokens.get("client_id") and twitch_tokens.get("client_secret")
        )
        embed.add_field(
            name="IGDB",
            value=(
                f"{'Enabled' if data['igdb_enabled'] else 'Disabled'} — "
                f"credentials {'configured' if igdb_credentials else 'missing'}"
            ),
        )
        embed.add_field(
            name="SteamGridDB",
            value=(
                f"{'Enabled' if data['steamgriddb_enabled'] else 'Disabled'} — "
                f"API key {'configured' if steamgriddb_tokens.get('api_key') else 'missing'}"
            ),
        )
        embed.add_field(
            name="Approved roles",
            value=", ".join(role.mention for role in roles if role) or "None",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RoomAnnounce(bot))
