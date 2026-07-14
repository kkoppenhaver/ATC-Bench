"""Tool definitions in Anthropic Messages API schema (DESIGN §11.1).

All v1 positions expose the same transmit / strip / wait surface. Handoff tools
appear from TRACON up and are omitted here. Descriptions never overclaim: the
150 wpm channel cost is stated only where it is actually simulated (CD, until
P4.0a lands the shared channel model).
"""

from __future__ import annotations


def position_tools(position: str = "CD") -> list[dict]:
    if position == "CD":
        timing = "Consumes sim time at 150 wpm; the channel is half-duplex."
    else:
        timing = ("The channel is shared with pilots and Tower coordination; "
                  "transmissions at this position are not yet time-metered.")
    return [
        {
            "name": "transmit",
            "description": (
                "Broadcast one radio transmission on your frequency, in standard "
                f"phraseology, addressed to a callsign. One transmission per call. {timing}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        {
            "name": "strip_create",
            "description": "Create a flight strip for an aircraft in a bay.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "acid": {"type": "string"},
                    "bay": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["acid", "bay"],
            },
        },
        {
            "name": "strip_update",
            "description": "Merge a patch into an aircraft's strip fields.",
            "input_schema": {
                "type": "object",
                "properties": {"acid": {"type": "string"}, "patch": {"type": "object"}},
                "required": ["acid", "patch"],
            },
        },
        {
            "name": "strip_move",
            "description": "Move an aircraft's strip to a bay at an index.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "acid": {"type": "string"},
                    "bay": {"type": "string"},
                    "index": {"type": "integer"},
                },
                "required": ["acid", "bay", "index"],
            },
        },
        {
            "name": "strip_delete",
            "description": "Delete an aircraft's strip.",
            "input_schema": {
                "type": "object",
                "properties": {"acid": {"type": "string"}},
                "required": ["acid"],
            },
        },
        {
            "name": "bay_read",
            "description": (
                "Read your strip bays back. Returns the full bay ordering and every "
                "strip's fields — your externalized memory."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "wait",
            "description": "Yield until the next radar sweep or event. Use when no action is needed.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]


# Back-compat alias (the v1 CD surface).
CD_TOOLS = position_tools("CD")
