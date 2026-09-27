from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterator

from ..models import Event


@dataclass
class ScrapeContext:
    """What the pipeline hands to a source for one run."""

    horizon: str  # YYYY-MM-DD; sources may stop once events start after this
    today: str  # YYYY-MM-DD, Tokyo local date
    # Returns True when the source can skip fetching details for this item
    # because the stored copy has the same fingerprint and is fresh.
    is_unchanged: Callable[[str, str], bool]
    max_pages: int = 60


@dataclass
class Unchanged:
    """Yielded instead of an Event when the stored copy is still valid."""

    id: str
    # Categories seen on the listing, so taxonomy changes apply without re-fetching details.
    source_categories: list[str] | None = None
    title: str = ""  # lets the pipeline infer a type when the source gives none


class EventSource(ABC):
    name: str  # registry key, also stored on every event
    label: str  # human name shown in the UI
    short: str  # 2-letter monogram for compact UI badges
    color: str  # badge colour in the UI
    homepage: str
    enabled: bool = True
    # Lower wins when duplicates across sources are merged: its record is the
    # base and others only fill in missing fields.
    priority: int = 50

    @abstractmethod
    def scrape(self, ctx: ScrapeContext) -> Iterator[Event | Unchanged]:
        """Yield normalized events (or Unchanged markers) for this source."""
