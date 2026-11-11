"""Stable filters over the original catalog, before any cloud mutation."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from fnmatch import fnmatchcase


def attrs(row):
    return next((x for x in reversed(row) if isinstance(x, dict)), {})


def positive_int(value):
    n = int(value)
    if n < 1:
        raise ValueError("Must be a positive integer")
    return n


def year_value(value):
    n = datetime.now().year if value == "current" else int(value)
    if not 1 <= n <= 9999:
        raise ValueError("Year must be current or an integer from 1 to 9999")
    return n


@dataclass
class Selection:
    years: list = field(default_factory=list)
    from_date: date | None = None
    to_date: date | None = None
    media: str = "all"
    albums: list = field(default_factory=list)
    filename: str | None = None
    min_bytes: int = 0
    exclude_favorites: bool = False
    exclude_archived: bool = False
    limit: int | None = None

    def __post_init__(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("--from-date must be on or before --to-date")
        if self.min_bytes < 0:
            raise ValueError("--min-size-mb must be nonnegative")
        if self.limit is not None and self.limit < 1:
            raise ValueError("--limit must be positive")

    @property
    def filtered(self):
        return bool(
            self.years
            or self.from_date
            or self.to_date
            or self.media != "all"
            or self.albums
            or self.filename
            or self.min_bytes
            or self.exclude_favorites
            or self.exclude_archived
            or self.limit
        )

    def matches(self, row, ext):
        e = ext[0] if ext and ext[0] else []
        if len(e) < 6:
            raise ValueError(
                "Missing catalog metadata; run catalog before selecting media"
            )
        a = attrs(row)
        video = "76647426" in a
        if self.media == "images" and video or self.media == "videos" and not video:
            return False
        if self.years or self.from_date or self.to_date:
            if not isinstance(e[3], (float, int)):
                return False
            # Google records timestamp and capture UTC offset in milliseconds.
            captured = (
                datetime.fromtimestamp(e[3] / 1000, timezone.utc)
                + timedelta(milliseconds=e[4] or 0)
            ).date()
            if self.years and captured.year not in self.years:
                return False
            if self.from_date and captured < self.from_date:
                return False
            if self.to_date and captured > self.to_date:
                return False
        memberships = {album[0] for album in (e[19] or [])} if len(e) > 19 else set()
        if self.albums and not memberships.intersection(self.albums):
            return False
        if self.filename and not fnmatchcase(
            (e[2] or "").casefold(), self.filename.casefold()
        ):
            return False
        if (e[5] or 0) < self.min_bytes:
            return False
        if self.exclude_favorites and a.get("163238866", [False])[0]:
            return False
        if self.exclude_archived and len(row) > 13 and row[13]:
            return False
        return True

    def select(self, inventory, catalog):
        rows = [
            r for r in inventory["items"] if self.matches(r, catalog["items"].get(r[0]))
        ]
        return rows[: self.limit] if self.limit else rows


def from_args(args):
    return Selection(
        years=args.year or [],
        from_date=args.from_date,
        to_date=args.to_date,
        media=args.media,
        albums=args.album or [],
        filename=args.filename,
        min_bytes=int(args.min_size_mb * 1_000_000),
        exclude_favorites=args.exclude_favorites,
        exclude_archived=args.exclude_archived,
        limit=args.limit,
    )
