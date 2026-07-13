from roomannounce.models import (
    automatic_metadata_ready,
    default_room_state,
    game_names_match,
    normalize_game_name,
    preset_game_name,
    preview_should_be_visible,
    resolve_fields,
)


def test_normalize_and_alias_matching():
    assert normalize_game_name("Tom Clancy's Rainbow Six® Siege") == "tomclancysrainbowsixsiege"
    assert game_names_match("Rocket League", ["rocket-league", "RL"])
    assert not game_names_match("Rocket League", ["Rogue Legacy"])


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
