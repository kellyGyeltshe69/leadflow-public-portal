from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import get_settings

STATE_TIMEZONES = {
    "CA": "America/Los_Angeles", "OR": "America/Los_Angeles", "WA": "America/Los_Angeles", "NV": "America/Los_Angeles",
    "AZ": "America/Phoenix", "CO": "America/Denver", "UT": "America/Denver", "NM": "America/Denver", "MT": "America/Denver",
    "TX": "America/Chicago", "IL": "America/Chicago", "AL": "America/Chicago", "WI": "America/Chicago", "MN": "America/Chicago",
    "MO": "America/Chicago", "LA": "America/Chicago", "OK": "America/Chicago", "KS": "America/Chicago", "IA": "America/Chicago",
    "NY": "America/New_York", "NJ": "America/New_York", "PA": "America/New_York", "OH": "America/New_York", "NC": "America/New_York",
    "SC": "America/New_York", "GA": "America/New_York", "FL": "America/New_York", "VA": "America/New_York", "MA": "America/New_York",
    "HI": "Pacific/Honolulu", "AK": "America/Anchorage",
}


def timezone_for_state(state: str) -> str:
    return STATE_TIMEZONES.get((state or "").upper(), get_settings().send_timezone_fallback)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(get_settings().send_timezone_fallback)


def next_business_send(base_utc: datetime, timezone_name: str, day_offset: int = 0) -> datetime:
    """Return a naive UTC datetime inside the configured recipient-local weekday window."""
    settings = get_settings()
    if base_utc.tzinfo is None:
        base_utc = base_utc.replace(tzinfo=timezone.utc)
    tz = _zone(timezone_name)
    local = base_utc.astimezone(tz) + timedelta(days=day_offset)

    # Keep follow-ups near 10:15 local; initial messages go to the next allowed time.
    target = local.replace(hour=max(settings.send_window_start_hour, 10), minute=15, second=0, microsecond=0)
    if day_offset == 0 and local <= target:
        candidate = target
    elif day_offset == 0 and settings.send_window_start_hour <= local.hour < settings.send_window_end_hour:
        candidate = local.replace(second=0, microsecond=0) + timedelta(minutes=5)
    else:
        candidate = target + timedelta(days=1 if local > target else 0)

    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    if candidate.hour < settings.send_window_start_hour:
        candidate = candidate.replace(hour=settings.send_window_start_hour, minute=15)
    if candidate.hour >= settings.send_window_end_hour:
        candidate = (candidate + timedelta(days=1)).replace(hour=max(settings.send_window_start_hour, 10), minute=15)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def in_business_window(now_utc: datetime, timezone_name: str) -> bool:
    settings = get_settings()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local = now_utc.astimezone(_zone(timezone_name))
    return local.weekday() < 5 and settings.send_window_start_hour <= local.hour < settings.send_window_end_hour
