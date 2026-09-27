"""Event source registry.

To add a source: create a module here with an `EventSource` subclass decorated
with `@register`, and import it at the bottom of this file.
"""

from __future__ import annotations

from .base import EventSource

REGISTRY: dict[str, type[EventSource]] = {}


def register(cls: type[EventSource]) -> type[EventSource]:
    REGISTRY[cls.name] = cls
    return cls


def get_source(name: str) -> type[EventSource]:
    try:
        return REGISTRY[name]
    except KeyError:
        raise SystemExit(f"Unknown source {name!r}. Known: {', '.join(sorted(REGISTRY))}")


def enabled_sources() -> list[type[EventSource]]:
    return [cls for cls in REGISTRY.values() if cls.enabled]


from . import tokyoartbeat, tokyocheapo, tokyoweekender  # noqa: E402,F401  (register themselves)
