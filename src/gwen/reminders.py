import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = "America/Guatemala"
_REMINDER_PREFIX = re.compile(r"^\s*(?:recuérdame|recuerdame)\s+", re.IGNORECASE)
_REMINDER_PATTERN = re.compile(
    r"^(?P<content>.+?)\s+(?:(?:el\s+)?(?P<day>hoy|mañana|\d{4}-\d{2}-\d{2})\s+)?"
    r"(?:a\s+las\s+)?(?P<time>\d{1,2}:\d{2})(?:\s*(?P<meridiem>a\.?m\.?|p\.?m\.?))?\s*$",
    re.IGNORECASE,
)
_TIME_ONLY_PATTERN = re.compile(
    r"^\s*\d{1,2}:\d{2}(?:\s*(?:a\.?m\.?|p\.?m\.?))?\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class ReminderRequest:
    content: str
    due_at: datetime | None
    timezone: str = TIMEZONE

    @property
    def needs_clarification(self) -> bool:
        return self.due_at is None


def parse_reminder_request(text: str, now: datetime | None = None) -> ReminderRequest | None:
    """Parse only explicit Spanish one-time reminders without consulting a provider."""
    prefix = _REMINDER_PREFIX.match(text)
    if not prefix:
        return None
    reminder_text = text[prefix.end() :].strip()
    match = _REMINDER_PATTERN.fullmatch(reminder_text)
    if not match or not match.group("day"):
        return ReminderRequest(content=reminder_text, due_at=None)
    zone = ZoneInfo(TIMEZONE)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    day = match.group("day").casefold()
    if day == "hoy":
        due_date = local_now.date()
    elif day == "mañana":
        due_date = local_now.date() + timedelta(days=1)
    else:
        try:
            due_date = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            return ReminderRequest(content=reminder_text, due_at=None)
    try:
        hour, minute = (int(value) for value in match.group("time").split(":"))
        meridiem = match.group("meridiem")
        if meridiem:
            if not 1 <= hour <= 12:
                return ReminderRequest(content=reminder_text, due_at=None)
            if meridiem.casefold().startswith("a"):
                hour %= 12
            elif hour != 12:
                hour += 12
        due_local = datetime(due_date.year, due_date.month, due_date.day, hour, minute, tzinfo=zone)
    except ValueError:
        return ReminderRequest(content=reminder_text, due_at=None)
    if due_local <= local_now:
        return ReminderRequest(content=reminder_text, due_at=None)
    return ReminderRequest(
        content=match.group("content").strip(), due_at=due_local.astimezone(UTC)
    )


def complete_reminder_time(pending_text: str, time_text: str) -> ReminderRequest | None:
    if not _TIME_ONLY_PATTERN.fullmatch(time_text):
        return None
    return parse_reminder_request(f"{pending_text.strip()} a las {time_text.strip()}")


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def in_reminder_timezone(value: datetime, timezone: str) -> datetime:
    return as_utc(value).astimezone(ZoneInfo(timezone))
