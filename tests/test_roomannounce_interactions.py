import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from discord import app_commands

from roomannounce.roomannounce import RoomAnnounce
from roomannounce.views import RSVPView


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.sent = []

    async def defer(self, **kwargs):
        self.deferred = True

    def is_done(self):
        return self.deferred or bool(self.sent)

    async def send_message(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeSetting:
    def __init__(self, value=False):
        self.value = value

    async def set(self, value):
        self.value = value


class FakeGuildConfig:
    def __init__(self):
        self.auto_tag = FakeSetting()


class FakeConfig:
    def __init__(self):
        self.group = FakeGuildConfig()

    def guild(self, guild):
        return self.group


class FakeMember:
    def __init__(self, user_id, display_name):
        self.id = user_id
        self.display_name = display_name


class FakeGuild:
    def __init__(self, members=None):
        self.id = 456
        self.default_role = object()
        self.members = {member.id: member for member in members or []}

    def get_member(self, user_id):
        return self.members.get(user_id)


class FakeChannel:
    def __init__(self, guild, member_count=4, user_limit=0):
        self.id = 123
        self.guild = guild
        self.members = [object()] * member_count
        self.user_limit = user_limit
        self.mention = "<#123>"
        self.default_overwrite = SimpleNamespace(view_channel=None, connect=None)

    def overwrites_for(self, role):
        return self.default_overwrite


def test_activehours_and_rsvp_subcommands_are_registered():
    commands = {command.name: command for command in RoomAnnounce.roomannounce_group.commands}
    assert {"activehours", "rsvp", "autotag", "settings"} <= commands.keys()
    assert {command.name for command in commands["activehours"].commands} == {
        "set",
        "enable",
        "force",
        "clear",
        "settings",
    }
    assert {command.name for command in commands["rsvp"].commands} == {
        "enable",
        "names",
        "settings",
    }


def test_every_roomannounce_slash_command_defers_before_io():
    source = Path("roomannounce/roomannounce.py").read_text()
    tree = ast.parse(source)
    command_groups = {"roomannounce_group", "activehours_group", "rsvp_group"}
    commands = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        is_command = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id in command_groups
            and decorator.func.attr == "command"
            for decorator in node.decorator_list
        )
        if is_command:
            commands.append(node.name)
            assert node.body
            assert ast.unparse(node.body[0]).startswith("await interaction.response.defer(")
    assert commands


def test_autotag_defers_and_persists_before_followup():
    cog = object.__new__(RoomAnnounce)
    cog.config = FakeConfig()
    interaction = SimpleNamespace(guild=object(), response=FakeResponse(), followup=FakeFollowup())
    asyncio.run(RoomAnnounce.autotag.callback(cog, interaction, True))
    assert interaction.response.deferred
    assert cog.config.group.auto_tag.value is True
    assert "future initial or new-game posts" in interaction.followup.sent[0][0]


def test_application_command_error_always_responds_ephemerally():
    cog = object.__new__(RoomAnnounce)
    interaction = SimpleNamespace(
        command=SimpleNamespace(qualified_name="roomannounce autotag"),
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=2),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )
    asyncio.run(cog.cog_app_command_error(interaction, app_commands.AppCommandError("failure")))
    assert interaction.response.sent
    assert interaction.response.sent[0][1]["ephemeral"] is True


def test_public_embed_room_size_role_removal_and_rsvp_summary():
    members = [FakeMember(index, f"Player {index}") for index in range(1, 8)]
    guild = FakeGuild(members)
    channel = FakeChannel(guild, member_count=4, user_limit=0)
    state = {
        "owner_id": None,
        "resolved": {"game_name": "Portal 2", "role_id": 999},
        "rsvp_responses": {
            "1": "join",
            "2": "join",
            "3": "join",
            "4": "join",
            "5": "join",
            "6": "join",
            "7": "maybe",
        },
    }
    cog = object.__new__(RoomAnnounce)
    embed = cog._build_embed(
        channel,
        state,
        public=True,
        guild_settings={"rsvp_enabled": True, "rsvp_show_names": True},
    )
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Room size"] == "4"
    assert "Announcement role" not in fields
    assert "+1 more" in fields["Joining"]
    assert fields["Maybe"].startswith("**1**")
    field_names = [field.name for field in embed.fields]
    assert field_names[-4:] == ["\u200b", "Joining", "Maybe", "Not Coming"]
    assert all(field.inline for field in embed.fields[-3:])

    channel.user_limit = 6
    embed = cog._build_embed(channel, state, public=False)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Room size"] == "4/6"
    assert "Announcement role" not in fields


def test_rsvp_view_is_persistent_and_participant_lists_paginate():
    view = RSVPView(object(), 456, 123)
    assert view.timeout is None
    assert {item.custom_id for item in view.children if item.custom_id} == {
        "roomannounce:rsvp:join",
        "roomannounce:rsvp:maybe",
        "roomannounce:rsvp:not_coming",
        "roomannounce:rsvp:participants",
    }
    link = next(item for item in view.children if item.custom_id is None)
    assert link.label == "Connect"
    assert link.url == "https://discord.com/channels/456/123"
    assert link.disabled is False

    locked_view = RSVPView(object(), 456, 123, locked=True)
    locked_link = next(item for item in locked_view.children if item.custom_id is None)
    assert locked_link.label == "Locked"
    assert locked_link.disabled is True
    assert locked_view.is_persistent()

    members = [FakeMember(index, f"Player {index} " + "x" * 80) for index in range(1, 101)]
    guild = FakeGuild(members)
    responses = {str(member.id): "join" for member in members}
    cog = object.__new__(RoomAnnounce)
    pages = cog._participant_pages(guild, responses)
    assert len(pages) > 1
    assert all(len(page) <= 3500 for page in pages)
    assert sum(page.count("• Player") for page in pages) == 100


def test_concurrent_rsvp_updates_are_retained_and_notify_once():
    cog = object.__new__(RoomAnnounce)
    cog._rsvp_locks = {}
    state = {
        "identity": "portal2",
        "rsvp_identity": "portal2",
        "rsvp_responses": {},
        "rsvp_milestones": [],
    }
    saved = []
    notifications = []

    async def context(interaction, channel_id):
        return object(), state, None

    async def save(channel_id, new_state):
        saved.append(dict(new_state["rsvp_responses"]))

    async def publish(channel):
        return object()

    async def notify(channel, current_state, milestone):
        notifications.append(milestone)

    cog._rsvp_context = context
    cog._save_state = save
    cog.publish_room = publish
    cog._notify_rsvp_milestone = notify

    def interaction(user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=FakeResponse(),
            followup=FakeFollowup(),
        )

    first = interaction(1)
    second = interaction(2)

    async def run_updates():
        await asyncio.gather(
            cog.set_rsvp(first, 123, "join"),
            cog.set_rsvp(second, 123, "maybe"),
        )

    asyncio.run(run_updates())
    assert state["rsvp_responses"] == {"1": "join", "2": "maybe"}
    assert saved[-1] == {"1": "join", "2": "maybe"}
    assert notifications == [1]


def test_stale_rsvp_message_is_rejected_without_mutation():
    cog = object.__new__(RoomAnnounce)
    cog._rsvp_locks = {}

    async def context(interaction, channel_id):
        return None, None, "This announcement is no longer current."

    cog._rsvp_context = context
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1), response=FakeResponse(), followup=FakeFollowup()
    )
    asyncio.run(cog.set_rsvp(interaction, 123, "join"))
    assert interaction.response.deferred
    assert "no longer current" in interaction.followup.sent[0][0]


def test_rsvp_view_rejects_bots_immediately():
    view = RSVPView(object(), 456, 123)
    interaction = SimpleNamespace(user=SimpleNamespace(bot=True), response=FakeResponse())
    assert asyncio.run(view.interaction_check(interaction)) is False
    assert interaction.response.sent[0][0] == "Bots cannot RSVP."


def test_locked_public_embed_reports_access_restriction():
    guild = FakeGuild()
    channel = FakeChannel(guild)
    channel.default_overwrite.connect = False
    cog = object.__new__(RoomAnnounce)
    embed = cog._build_embed(
        channel,
        {"resolved": {"game_name": "Portal 2"}},
        public=True,
        guild_settings={"rsvp_enabled": False},
    )
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Room access"].startswith("🔒 Locked")


def test_hidden_room_suspends_and_restores_public_announcement():
    guild = FakeGuild()
    channel = FakeChannel(guild)
    channel.default_overwrite.view_channel = False
    state = {
        "public_message_id": 99,
        "public_channel_id": 88,
        "hidden_public_suspended": False,
    }
    deleted = []
    resolved = []

    cog = object.__new__(RoomAnnounce)

    async def get_state(channel_id):
        return state

    async def delete_public(current_channel, current_state, suppress):
        deleted.append((current_state["hidden_public_suspended"], suppress))
        current_state["public_message_id"] = None

    async def no_op(channel):
        return None

    async def resolve(channel):
        resolved.append(channel.id)

    cog.get_state = get_state
    cog._delete_public = delete_public
    cog.ensure_preview = no_op
    cog.refresh_control = no_op
    cog.resolve_room = resolve

    asyncio.run(cog._handle_room_access_change(channel))
    assert deleted == [(True, False)]
    assert state["hidden_public_suspended"] is True
    assert resolved == []

    channel.default_overwrite.view_channel = None
    asyncio.run(cog._handle_room_access_change(channel))
    assert resolved == [123]


def test_hidden_room_rejects_manual_publication():
    guild = FakeGuild()
    channel = FakeChannel(guild)
    channel.default_overwrite.view_channel = False
    state = {
        "announcements_enabled": True,
        "public_message_id": None,
        "resolved": {"game_name": "Portal 2"},
    }
    cog = object.__new__(RoomAnnounce)

    async def get_state(channel_id):
        return state

    cog.get_state = get_state
    with pytest.raises(RuntimeError, match="Hidden rooms"):
        asyncio.run(cog.publish_room(channel))
