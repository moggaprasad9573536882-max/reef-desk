"""Time helpers. Cairns uses AEST (UTC+10) all year: Queensland has no daylight saving."""
from datetime import date, datetime, timedelta, timezone

AEST = timezone(timedelta(hours=10), "AEST")
OPEN_HOUR = 8     # office opens 8:00 am
CLOSE_HOUR = 18   # office closes 6:00 pm


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def today_local(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(AEST).date()


def reply_by(received: datetime) -> datetime:
    """The time we promise a human reply: 1 hour inside office hours,
    otherwise 9:00 am on the next open morning."""
    local = received.astimezone(AEST)
    closing = local.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
    if local.hour >= OPEN_HOUR and local + timedelta(hours=1) <= closing:
        return local + timedelta(hours=1)
    if local.hour < OPEN_HOUR:
        return local.replace(hour=OPEN_HOUR + 1, minute=0, second=0, microsecond=0)
    return (local + timedelta(days=1)).replace(hour=OPEN_HOUR + 1, minute=0, second=0, microsecond=0)


def fmt_local(dt: datetime | str | None, with_day: bool = True) -> str:
    """'Sat 17 Oct, 3:05 pm' in Cairns time (portable: no %-I, which fails on Windows)."""
    if not dt:
        return ""
    if isinstance(dt, str):
        dt = parse(dt)
    local = dt.astimezone(AEST)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    return f"{local:%a %d %b}, {clock}" if with_day else clock


def age_text(since: datetime | str, now: datetime | None = None) -> str:
    if isinstance(since, str):
        since = parse(since)
    mins = int(((now or utcnow()) - since).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 0)} min"
    if mins < 48 * 60:
        return f"{mins // 60} h {mins % 60:02d} m"
    return f"{mins // 1440} days"
