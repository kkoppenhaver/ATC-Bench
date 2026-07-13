"""Tool definitions in Anthropic Messages API schema (DESIGN §11.1).

The CD position exposes the transmit / strip / wait surface. Handoff tools appear
from TRACON up and are omitted here.
"""

from __future__ import annotations

CD_TOOLS = [
    {
        "name": "transmit",
        "description": (
            "Broadcast one radio transmission on your frequency. One transmission per "
            "call. Consumes sim time at 150 wpm; the channel is half-duplex."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "strip_create",
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
        "input_schema": {
            "type": "object",
            "properties": {"acid": {"type": "string"}, "patch": {"type": "object"}},
            "required": ["acid", "patch"],
        },
    },
    {
        "name": "strip_move",
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
        "input_schema": {
            "type": "object",
            "properties": {"acid": {"type": "string"}},
            "required": ["acid"],
        },
    },
    {
        "name": "bay_read",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "wait",
        "description": "Yield until the next radar sweep or event. Use when no action is needed.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
