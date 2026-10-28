"""Zambia has no daylight saving, so a fixed UTC+2 offset is correct (and needs no tz database on Windows).
Store UTC ISO strings; show Africa/Lusaka local time."""
from datetime import date, datetime, timedelta, timezone

LUSAKA = timezone(timedelta(hours=2))
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FMT)


def local_to_utc_iso(d: date, hh: int, mm: int = 0) -> str:
    return to_utc_iso(datetime(d.year, d.month, d.day, hh, mm, tzinfo=LUSAKA))


def parse_utc(iso: str) -> datetime:
    return datetime.strptime(iso, _FMT).replace(tzinfo=timezone.utc)


def to_local(iso: str) -> datetime:
    return parse_utc(iso).astimezone(LUSAKA)


def local_date(iso: str) -> date:
    return to_local(iso).date()


def fmt_local(iso: str) -> str:
    return to_local(iso).strftime("%a %d %b %H:%M")


def lusaka_today() -> date:
    return datetime.now(LUSAKA).date()


def upcoming_days(today: date, n: int = 8) -> str:
    return ", ".join((today + timedelta(days=i)).strftime("%a %Y-%m-%d") for i in range(n))