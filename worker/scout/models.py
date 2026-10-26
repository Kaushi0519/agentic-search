from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    """A role as reported by one source, before filtering or dedup."""

    source: str
    source_id: str
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    category: str | None = None
    terms: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    # Only some sources report this; None means unknown.
    degrees: list[str] | None = None


@dataclass
class Role:
    """A unique role as stored in the database."""

    id: int
    company: str
    title: str
    url: str
    locations: list[str]
    first_seen_at: datetime
