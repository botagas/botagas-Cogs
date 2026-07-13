import re
from typing import Any, Dict, Iterable, Optional

MISSING_GAME = "Missing — enter manually or select a preset"
MISSING_PARTY = "Unknown — Rich Presence did not provide party information"
MISSING_ROLE = "No matching role — announcement will not tag a role"


def normalize_game_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def game_names_match(name: Optional[str], candidates: Iterable[Optional[str]]) -> bool:
    normalized = normalize_game_name(name)
    return bool(normalized) and any(
        normalized == normalize_game_name(candidate) for candidate in candidates
    )


def preset_game_name(preset: Dict[str, Any]) -> str:
    return preset.get("game_name") or preset.get("title") or ""


def default_room_state(owner_id: int) -> Dict[str, Any]:
    return {
        "owner_id": owner_id,
        "control_message_id": None,
        "preview_message_id": None,
        "preview_mode": "auto",
        "public_message_id": None,
        "public_channel_id": None,
        "announcements_enabled": True,
        "selected_preset": None,
        "source_choice": "auto",
        "detected": {},
        "provider": {},
        "provider_diagnostics": [],
        "manual_overrides": {},
        "selected_role_id": None,
        "resolved": {},
        "auto_eligible": False,
        "identity": None,
        "suppressed_identity": None,
        "missing_role_warned_identity": None,
        "tagged_identity": None,
        "last_error": None,
    }


def resolve_fields(
    preset_name: Optional[str],
    preset: Dict[str, Any],
    detected: Dict[str, Any],
    provider: Dict[str, Any],
    manual: Dict[str, Any],
    selected_role_id: Optional[int],
) -> Dict[str, Any]:
    preset_game = preset_game_name(preset)
    detected_game = detected.get("name") or ""
    preset_aliases = preset.get("game_aliases") or []
    detected_matches_preset = bool(preset_game) and game_names_match(
        detected_game, [preset_game, *preset_aliases]
    )

    manual_game = manual.get("game_name") or ""
    if manual_game:
        game_name = manual_game
        source = "Manual override"
    elif preset_game:
        game_name = preset_game
        source = "Preset"
    elif detected_game:
        game_name = detected_game
        source = "Discord Rich Presence"
    else:
        game_name = ""
        source = "None"

    description = preset.get("announcement_description") or ""
    image_url = preset.get("announcement_image_url") or ""
    party = ""
    provider_url = ""

    detected_matches_active_game = game_names_match(
        detected_game, [game_name, *(preset_aliases if preset_game == game_name else [])]
    )
    if detected_game and detected_matches_active_game:
        description = description or detected.get("description") or ""
        image_url = image_url or detected.get("image_url") or ""
        party = detected.get("party") or ""

    if provider and game_names_match(provider.get("name"), [game_name]):
        game_name = provider.get("name") or game_name
        description = description or provider.get("description") or ""
        image_url = image_url or provider.get("image_url") or ""
        provider_url = provider.get("url") or ""
        if source == "None":
            source = provider.get("source") or "Provider"

    resolved = {
        "game_name": game_name,
        "source": source,
        "description": description,
        "image_url": image_url,
        "party": party,
        "note": "",
        "role_id": selected_role_id or preset.get("announcement_role_id"),
        "provider_url": provider_url,
        "preset_name": preset_name,
        "detected_conflict": bool(preset_game and detected_game and not detected_matches_preset),
        "detected_game": detected_game,
    }
    for field, value in manual.items():
        if value not in (None, ""):
            resolved[field] = value
    if manual:
        resolved["source"] = "Manual override"
    resolved["identity"] = normalize_game_name(resolved.get("game_name")) or None
    return resolved


def has_game_context(resolved: Dict[str, Any]) -> bool:
    return bool(normalize_game_name(resolved.get("game_name")))


def preview_should_be_visible(state: Dict[str, Any]) -> bool:
    if not has_game_context(state.get("resolved") or {}):
        return False
    mode = state.get("preview_mode", "auto")
    if mode == "visible":
        return True
    if mode == "hidden":
        return False
    return bool(state.get("announcements_enabled", True))


def automatic_metadata_ready(
    resolved: Dict[str, Any],
    preset: Dict[str, Any],
    detected: Dict[str, Any],
    provider: Dict[str, Any],
) -> bool:
    if not has_game_context(resolved):
        return False
    if preset_game_name(preset):
        return True
    if provider.get("name") and game_names_match(
        provider.get("name"), [resolved.get("game_name")]
    ):
        return True
    return bool(
        detected.get("application_id")
        and (detected.get("description") or detected.get("image_url") or detected.get("party"))
    )
