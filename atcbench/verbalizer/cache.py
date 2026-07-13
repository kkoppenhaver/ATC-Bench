"""Verbalizer response cache and pluggable backend (DESIGN §8.1, §8.3, principle #2).

The verbalizer converts FSM intent -> one radio string. The design pins an LLM at
temp 0 and caches every output keyed on ``(intent_json, persona, prompt_hash)`` so
repeat runs never hit the API and behavior is perfectly reproducible.

This module provides that cache and the backend seam:

- ``VerbalizerBackend`` — anything with ``render(intent) -> str`` (the template
  verbalizer is the default, deterministic, no-API backend; an LLM backend can slot
  in to *populate* the cache).
- ``ResponseCache`` — an on-disk JSON map from cache key to rendered string. Ships
  with a run so the run is reproducible without the backend.
- ``CachedVerbalizer`` — cache-first wrapper: return the cached string if present,
  else call the backend and store it.

The cache key includes a pinned verbalizer prompt version so that changing the
verbalizer's prompt (a benchmark-defining change) invalidates the cache rather than
silently reusing stale wording.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

VERBALIZER_PROMPT_VERSION = "vb-template-v1"


class VerbalizerBackend(Protocol):
    def render(self, intent: dict) -> str: ...  # pragma: no cover - protocol


def cache_key(intent: dict, prompt_version: str = VERBALIZER_PROMPT_VERSION) -> str:
    """Deterministic key for an intent. Persona lives inside ``intent`` already."""
    canonical = json.dumps(intent, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{prompt_version}\x00{canonical}".encode()).hexdigest()[:32]


class ResponseCache:
    def __init__(self, entries: dict[str, str] | None = None,
                 prompt_version: str = VERBALIZER_PROMPT_VERSION):
        self.prompt_version = prompt_version
        self._entries: dict[str, str] = dict(entries or {})
        self.hits = 0
        self.misses = 0

    def get(self, intent: dict) -> str | None:
        val = self._entries.get(cache_key(intent, self.prompt_version))
        if val is None:
            self.misses += 1
        else:
            self.hits += 1
        return val

    def put(self, intent: dict, rendered: str) -> None:
        self._entries[cache_key(intent, self.prompt_version)] = rendered

    def to_dict(self) -> dict:
        return {"prompt_version": self.prompt_version, "entries": dict(sorted(self._entries.items()))}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "ResponseCache":
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        return ResponseCache(obj.get("entries", {}), obj.get("prompt_version", VERBALIZER_PROMPT_VERSION))

    def __len__(self) -> int:
        return len(self._entries)


class CachedVerbalizer:
    """Cache-first verbalizer. Behaviour is identical to the backend; the cache only
    memoizes so runs are reproducible without re-invoking the backend."""

    def __init__(self, backend: VerbalizerBackend, cache: ResponseCache | None = None):
        self.backend = backend
        self.cache = cache or ResponseCache()

    def render(self, intent: dict) -> str:
        cached = self.cache.get(intent)
        if cached is not None:
            return cached
        rendered = self.backend.render(intent)
        self.cache.put(intent, rendered)
        return rendered
