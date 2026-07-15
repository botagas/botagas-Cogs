import re
from datetime import datetime
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MISSING_GAME = "Missing — enter manually or select a preset"
MISSING_PARTY = "Unknown — Rich Presence did not provide party information"
RSVP_STATUSES = ("join", "maybe", "not_coming")
RSVP_MILESTONES = (1, 5, 10, 25, 50, 100, 200, 500, 1000)
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
SENTENCE_ABBREVIATIONS = {
    "dr.",
    "e.g.",
    "etc.",
    "i.e.",
    "inc.",
    "jr.",
    "ltd.",
    "mr.",
    "mrs.",
    "ms.",
    "no.",
    "sr.",
    "st.",
    "u.k.",
    "u.s.",
    "vs.",
}


def normalize_game_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def first_sentence(value: Optional[str]) -> str:
    text = " ".join((value or "").split())
    if not text:
        return ""
    closing_characters = "\"'’”)]}"
    for index, character in enumerate(text):
        if character not in ".!?":
            continue
        end = index + 1
        while end < len(text) and text[end] in closing_characters:
            end += 1
        if end < len(text) and not text[end].isspace():
            continue
        token = text[: index + 1].rsplit(maxsplit=1)[-1].casefold()
        if character == "." and token in SENTENCE_ABBREVIATIONS:
            continue
        if (
            character == "."
            and index > 0
            and end < len(text)
            and text[index - 1].isdigit()
            and text[end].isdigit()
        ):
            continue
        return text[:end]
    return text


def game_names_match(name: Optional[str], candidates: Iterable[Optional[str]]) -> bool:
    normalized = normalize_game_name(name)
    return bool(normalized) and any(
        normalized == normalize_game_name(candidate) for candidate in candidates
    )


def preset_game_name(preset: Dict[str, Any]) -> str:
    return preset.get("game_name") or preset.get("title") or ""


def announcement_destination_id(
    state: Dict[str, Any], guild_settings: Dict[str, Any]
) -> Optional[int]:
    source_channel_id = state.get("source_channel_id")
    destinations = guild_settings.get("announcement_channels") or {}
    if source_channel_id is not None:
        destination_id = destinations.get(str(source_channel_id))
        if destination_id:
            return destination_id
    return guild_settings.get("announcement_channel_id")


def parse_clock(value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (value or "").strip())
    if not match:
        raise ValueError("Times must use 24-hour `HH:MM` format.")
    hour, minute = (int(part) for part in match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("Times must use 24-hour `HH:MM` format.")
    return hour * 60 + minute


def parse_weekdays(value: Optional[str]) -> list[int]:
    if not value or not value.strip():
        return list(range(7))
    days = []
    for item in value.split(","):
        normalized = item.strip().casefold()
        if normalized not in WEEKDAY_ALIASES:
            raise ValueError(f"Unknown weekday `{item.strip()}`.")
        day = WEEKDAY_ALIASES[normalized]
        if day not in days:
            days.append(day)
    if not days:
        raise ValueError("Select at least one weekday.")
    return sorted(days)


def validate_timezone(value: str) -> str:
    timezone = (value or "").strip()
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use a valid IANA timezone such as `Europe/Vilnius`.") from exc
    return timezone


def active_hours_are_active(
    guild_settings: Dict[str, Any], now: Optional[datetime] = None
) -> bool:
    if not guild_settings.get("active_hours_enabled"):
        return True
    try:
        timezone = ZoneInfo(guild_settings.get("active_hours_timezone") or "")
        start = parse_clock(guild_settings.get("active_hours_start") or "")
        end = parse_clock(guild_settings.get("active_hours_end") or "")
    except (ZoneInfoNotFoundError, ValueError):
        return False
    if start == end:
        return False
    weekdays = set(guild_settings.get("active_hours_weekdays") or [])
    if now is None:
        local = datetime.now(timezone)
    elif now.tzinfo is None:
        local = now.replace(tzinfo=timezone)
    else:
        local = now.astimezone(timezone)
    current = local.hour * 60 + local.minute
    if start < end:
        return local.weekday() in weekdays and start <= current < end
    if current >= start:
        return local.weekday() in weekdays
    return current < end and (local.weekday() - 1) % 7 in weekdays


def tagging_is_allowed(
    guild_settings: Dict[str, Any], source: str, now: Optional[datetime] = None
) -> bool:
    if source == "automatic" and not guild_settings.get("auto_tag"):
        return False
    if source not in {"automatic", "manual"}:
        return False
    if not guild_settings.get("active_hours_enabled"):
        return True
    active = active_hours_are_active(guild_settings, now)
    if source == "automatic":
        return active
    return active or not guild_settings.get("active_hours_forced")


def is_new_game_post(
    had_public_announcement: bool,
    old_identity: Optional[str],
    new_identity: Optional[str],
) -> bool:
    return not had_public_announcement or old_identity != new_identity


def active_hours_days_text(days: Iterable[int]) -> str:
    selected = sorted(set(days))
    if selected == list(range(7)):
        return "Every day"
    return ", ".join(WEEKDAY_NAMES[day] for day in selected if 0 <= day <= 6) or "None"


def format_room_size(member_count: int, user_limit: int) -> str:
    return f"{member_count}/{user_limit}" if user_limit else str(member_count)


def rsvp_groups(responses: Dict[str, str]) -> Dict[str, list[int]]:
    groups = {status: [] for status in RSVP_STATUSES}
    for user_id, status in responses.items():
        if status in groups:
            try:
                groups[status].append(int(user_id))
            except (TypeError, ValueError):
                continue
    return groups


def consume_rsvp_milestones(
    consumed: Iterable[int], join_count: int
) -> tuple[Optional[int], list[int]]:
    consumed_set = {int(value) for value in consumed}
    newly_crossed = [
        milestone
        for milestone in RSVP_MILESTONES
        if milestone <= join_count and milestone not in consumed_set
    ]
    if not newly_crossed:
        return None, sorted(consumed_set)
    consumed_set.update(newly_crossed)
    return max(newly_crossed), sorted(consumed_set)


def default_room_state(owner_id: int) -> Dict[str, Any]:
    return {
        "owner_id": owner_id,
        "source_channel_id": None,
        "control_message_id": None,
        "preview_message_id": None,
        "preview_mode": "auto",
        "public_message_id": None,
        "public_channel_id": None,
        "hidden_public_suspended": False,
        "announcements_enabled": True,
        "selected_preset": None,
        "source_choice": "auto",
        "detected": {},
        "provider": {},
        "provider_diagnostics": [],
        "manual_overrides": {},
        "rsvp_identity": None,
        "rsvp_responses": {},
        "rsvp_milestones": [],
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
        description = description or first_sentence(provider.get("description"))
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
