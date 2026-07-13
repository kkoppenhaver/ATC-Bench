"""Bay/strip store and tool implementations (DESIGN §9.2, §9.3).

A minimal but faithful strip bay: named bays holding ordered ACID lists, plus a
per-ACID strip dict. Bay names are position-specific; the model may create custom
bays. Every mutation is snapshotted into strips_history for replay/scoring.
"""

from __future__ import annotations

import copy
from typing import Any


class StripStore:
    def __init__(self, bays: list[str] | None = None):
        self.bays: dict[str, list[str]] = {b: [] for b in (bays or ["queue", "cleared", "watch"])}
        self.strips: dict[str, dict[str, Any]] = {}
        self.history: list[dict] = []

    # --- tool ops (return a short result string for the tool router) ----------

    def strip_create(self, acid: str, bay: str, fields: dict | None = None) -> str:
        self.bays.setdefault(bay, [])
        if acid not in self.bays[bay]:
            self.bays[bay].append(acid)
        strip = {"acid": acid}
        strip.update(fields or {})
        self.strips[acid] = strip
        return f"strip created for {acid} in {bay}"

    def strip_update(self, acid: str, patch: dict) -> str:
        if acid not in self.strips:
            self.strips[acid] = {"acid": acid}
        _json_merge(self.strips[acid], patch)
        return f"strip {acid} updated"

    def strip_move(self, acid: str, bay: str, index: int) -> str:
        for b in self.bays.values():
            if acid in b:
                b.remove(acid)
        self.bays.setdefault(bay, [])
        index = max(0, min(index, len(self.bays[bay])))
        self.bays[bay].insert(index, acid)
        return f"strip {acid} moved to {bay}[{index}]"

    def strip_delete(self, acid: str) -> str:
        for b in self.bays.values():
            if acid in b:
                b.remove(acid)
        self.strips.pop(acid, None)
        return f"strip {acid} deleted"

    def bay_read(self) -> dict:
        return {"bays": copy.deepcopy(self.bays), "strips": copy.deepcopy(self.strips)}

    def snapshot(self, tick: int) -> None:
        self.history.append({"tick": tick, **self.bay_read()})


def _json_merge(target: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _json_merge(target[k], v)
        else:
            target[k] = v
