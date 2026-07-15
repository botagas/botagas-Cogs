from datetime import datetime, timezone

import pytest

from roomannounce.models import (
    active_hours_are_active,
    active_hours_days_text,
    announcement_destination_id,
    automatic_metadata_ready,
    consume_rsvp_milestones,
    default_room_state,
    first_sentence,
    format_room_size,
    game_names_match,
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


def test_announcement_destination_prefers_room_source_mapping_then_default():
    settings = {
        "announcement_channel_id": 10,
        "announcement_channels": {"20": 30},
    }
    assert announcement_destination_id({"source_channel_id": 20}, settings) == 30
    assert announcement_destination_id({"source_channel_id": 21}, settings) == 10
    assert announcement_destination_id({}, settings) == 10
    assert announcement_destination_id({"source_channel_id": 21}, {}) is None


def test_active_hours_validation_and_display():
    assert parse_clock("9:05") == 545
    assert parse_clock("23:59") == 1439
    with pytest.raises(ValueError):
        parse_clock("24:00")
    with pytest.raises(ValueError):
        parse_clock("noon")
    assert parse_weekdays(None) == list(range(7))
    assert parse_weekdays("Mon, Wednesday, fri") == [0, 2, 4]
    with pytest.raises(ValueError, match="Unknown weekday"):
        parse_weekdays("Funday")
    assert active_hours_days_text(range(7)) == "Every day"
    assert active_hours_days_text([0, 4]) == "Monday, Friday"
    assert validate_timezone("Europe/Vilnius") == "Europe/Vilnius"
    with pytest.raises(ValueError, match="IANA timezone"):
        validate_timezone("Moon/Sea_of_Tranquility")


def test_daily_active_hours_boundaries_and_tag_policy():
    settings = {
        "active_hours_enabled": True,
        "active_hours_timezone": "UTC",
        "active_hours_start": "09:00",
        "active_hours_end": "17:00",
        "active_hours_weekdays": [0],
        "active_hours_forced": True,
        "auto_tag": True,
    }
    assert not active_hours_are_active(settings, datetime(2026, 7, 13, 8, 59, tzinfo=timezone.utc))
    assert active_hours_are_active(settings, datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc))
    assert active_hours_are_active(settings, datetime(2026, 7, 13, 16, 59, tzinfo=timezone.utc))
    assert not active_hours_are_active(settings, datetime(2026, 7, 13, 17, 0, tzinfo=timezone.utc))
    assert tagging_is_allowed(
        settings, "automatic", datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    )
    assert not tagging_is_allowed(
        settings, "automatic", datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
    )
    assert not tagging_is_allowed(
        settings, "manual", datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
    )
    settings["active_hours_forced"] = False
    assert tagging_is_allowed(
        settings, "manual", datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
    )


def test_overnight_active_hours_use_starting_weekday_and_handle_dst():
    settings = {
        "active_hours_enabled": True,
        "active_hours_timezone": "Europe/Vilnius",
        "active_hours_start": "22:00",
        "active_hours_end": "03:00",
        "active_hours_weekdays": [4],
    }
    assert active_hours_are_active(settings, datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc))
    assert active_hours_are_active(settings, datetime(2026, 7, 17, 23, 30, tzinfo=timezone.utc))
    assert not active_hours_are_active(settings, datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc))

    settings.update(
        active_hours_start="01:00",
        active_hours_end="05:00",
        active_hours_weekdays=[6],
    )
    assert active_hours_are_active(settings, datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc))


def test_disabled_active_hours_do_not_restrict_tagging():
    settings = {
        "active_hours_enabled": False,
        "active_hours_forced": True,
        "auto_tag": True,
    }
    assert active_hours_are_active(settings)
    assert tagging_is_allowed(settings, "automatic")
    assert tagging_is_allowed(settings, "manual")
    settings["auto_tag"] = False
    assert not tagging_is_allowed(settings, "automatic")


def test_automatic_tagging_only_applies_to_initial_or_new_game_posts():
    assert is_new_game_post(False, None, "portal2")
    assert is_new_game_post(True, "portal2", "deep-rock-galactic")
    assert not is_new_game_post(True, "portal2", "portal2")


def test_room_size_rsvp_groups_and_milestones():
    assert format_room_size(4, 0) == "4"
    assert format_room_size(4, 6) == "4/6"
    groups = rsvp_groups(
        {"1": "join", "2": "maybe", "3": "not_coming", "broken": "join", "4": "bad"}
    )
    assert groups == {"join": [1], "maybe": [2], "not_coming": [3]}
    milestone, consumed = consume_rsvp_milestones([], 27)
    assert milestone == 25
    assert consumed == [1, 5, 10, 25]
    milestone, consumed = consume_rsvp_milestones(consumed, 9)
    assert milestone is None
    assert consumed == [1, 5, 10, 25]
    milestone, consumed = consume_rsvp_milestones(consumed, 1000)
    assert milestone == 1000
    assert consumed == [1, 5, 10, 25, 50, 100, 200, 500, 1000]

    state = default_room_state(42)
    assert state["rsvp_identity"] is None
    assert state["rsvp_responses"] == {}
    assert state["rsvp_milestones"] == []
    assert state["hidden_public_suspended"] is False


def test_normalize_and_alias_matching():
    assert normalize_game_name("Tom Clancy's Rainbow Six® Siege") == "tomclancysrainbowsixsiege"
    assert game_names_match("Rocket League", ["rocket-league", "RL"])
    assert not game_names_match("Rocket League", ["Rogue Legacy"])


def test_provider_description_summary_uses_one_sentence():
    assert first_sentence("First sentence. Second sentence.") == "First sentence."
    assert first_sentence('A quoted sentence!" Another sentence.') == 'A quoted sentence!"'
    assert first_sentence("Dr. Mario returns. Another sentence.") == "Dr. Mario returns."
    assert first_sentence("  A single sentence without punctuation  ") == (
        "A single sentence without punctuation"
    )

    provider_description = "Provider sentence one. Provider sentence two."
    resolved = resolve_fields(
        None,
        {},
        {"name": "Portal 2"},
        {"name": "Portal 2", "description": provider_description},
        {},
        None,
    )
    assert resolved["description"] == "Provider sentence one."

    resolved = resolve_fields(
        "portal",
        {
            "game_name": "Portal 2",
            "announcement_description": "Preset sentence one. Preset sentence two.",
        },
        {},
        {"name": "Portal 2", "description": provider_description},
        {},
        None,
    )
    assert resolved["description"] == "Preset sentence one. Preset sentence two."


def test_preset_identity_wins_and_matching_presence_enriches_it():
    resolved = resolve_fields(
        "rocket",
        {
            "game_name": "Rocket League",
            "game_aliases": ["RL"],
            "announcement_role_id": 42,
        },
        {
            "name": "RL",
            "description": "Ranked doubles",
            "party": "2/2",
            "image_url": "https://example.com/rich.png",
        },
        {},
        {},
        None,
    )
    assert resolved["game_name"] == "Rocket League"
    assert resolved["source"] == "Preset"
    assert resolved["description"] == "Ranked doubles"
    assert resolved["party"] == "2/2"
    assert resolved["role_id"] == 42
    assert not resolved["detected_conflict"]


def test_legacy_roomer_title_is_a_game_context():
    preset = {"title": "Deep Rock Galactic", "status": "Hazard 5", "limit": 4}
    assert preset_game_name(preset) == "Deep Rock Galactic"
    resolved = resolve_fields("drg", preset, {}, {}, {}, None)
    assert resolved["game_name"] == "Deep Rock Galactic"
    assert automatic_metadata_ready(resolved, preset, {}, {})


def test_conflicting_presence_does_not_replace_preset():
    resolved = resolve_fields(
        "rocket",
        {"game_name": "Rocket League", "game_aliases": []},
        {"name": "Fortnite", "description": "Battle Royale"},
        {"name": "Rocket League", "description": "Football with cars"},
        {},
        None,
    )
    assert resolved["game_name"] == "Rocket League"
    assert resolved["description"] == "Football with cars"
    assert resolved["detected_conflict"]


def test_manual_values_override_resolved_fields():
    resolved = resolve_fields(
        None,
        {},
        {"name": "Helldivers 2", "description": "Detected description"},
        {"name": "Helldivers 2", "description": "Provider description"},
        {"description": "Bring stratagems", "party": "3/4"},
        99,
    )
    assert resolved["description"] == "Bring stratagems"
    assert resolved["party"] == "3/4"
    assert resolved["role_id"] == 99
    assert resolved["source"] == "Manual override"


def test_manual_game_switch_uses_matching_provider_metadata():
    resolved = resolve_fields(
        "old-preset",
        {"game_name": "Old Game"},
        {"name": "Old Game", "description": "Old presence"},
        {"name": "New Game", "description": "New provider description"},
        {"game_name": "New Game"},
        None,
    )
    assert resolved["game_name"] == "New Game"
    assert resolved["description"] == "New provider description"
    assert resolved["source"] == "Manual override"


def test_preview_modes_and_disabled_manual_preview():
    state = default_room_state(1)
    state["resolved"] = {"game_name": "Deep Rock Galactic"}
    assert preview_should_be_visible(state)
    state["announcements_enabled"] = False
    assert not preview_should_be_visible(state)
    state["preview_mode"] = "visible"
    assert preview_should_be_visible(state)
    state["preview_mode"] = "hidden"
    state["announcements_enabled"] = True
    assert not preview_should_be_visible(state)


def test_preview_never_shows_without_game_context():
    state = default_room_state(1)
    state["preview_mode"] = "visible"
    assert not preview_should_be_visible(state)


def test_name_only_presence_is_not_automatic_metadata_ready():
    resolved = {"game_name": "Unknown Game"}
    assert not automatic_metadata_ready(
        resolved,
        {},
        {"name": "Unknown Game", "application_id": 123},
        {},
    )
    assert automatic_metadata_ready(
        resolved,
        {},
        {"name": "Unknown Game", "application_id": 123, "party": "1/4"},
        {},
    )
