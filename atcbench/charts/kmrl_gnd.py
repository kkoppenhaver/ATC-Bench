"""Marlow Regional (KMRL) — Ground chart pack (DESIGN §4.4, §5.1, §6.2).

⚠️ FICTIONAL FACILITY. Marlow Regional Airport (KMRL) does not exist; this surface
graph is fabricated for the benchmark, not a real airport diagram (see ``FACILITY_KIND``
and the note in kmrl_cd.py). It is designed to reproduce the *kind* of geometry that
makes Ground hard — a crossing runway and opposable taxiway flows — so the harness can
be exercised before a real FAA airport diagram is digitized (task P2.2).

The layout keeps the interesting difficulty: departures must **cross runway 31R** (an
active crossing runway) to reach the departure runway 31C, and departures/arrivals can
be misrouted into a head-on on the same taxiway.

Layout:
    Gates G1/G2 --taxiway A--> [cross RWY 31R] --> hold short RWY 31C   (departures)
    RWY exit    --taxiway B--> Gates G1/G2                              (arrivals)
    (a connector RWEX--A3 lets a *misrouted* arrival meet departures head-on on A)
"""

from __future__ import annotations

from ..sim.taxi import TaxiEdge, TaxiGraph, TaxiNode

FACILITY = "KMRL"  # fictional
FACILITY_NAME = "Marlow Regional Airport"
FACILITY_KIND = "fictional"
POSITION = "MRL_GND"
CALLSIGN = "Marlow Ground"

DEPARTURE_RUNWAY = "31C"
CROSSING_RUNWAY = "31R"
GATES = ["G1", "G2"]

_NODES = [
    TaxiNode("G1", "gate", 0.0, 0.0),
    TaxiNode("G2", "gate", 0.0, 0.4),
    TaxiNode("A1", "intersection", 0.3, 0.2),
    TaxiNode("A2", "intersection", 0.7, 0.2),
    TaxiNode("HS_31R", "hold_short", 0.85, 0.2),
    TaxiNode("RW_31R", "runway", 0.95, 0.2, guard_runway="31R"),
    TaxiNode("A3", "intersection", 1.1, 0.2),
    TaxiNode("HS_31C", "hold_short", 1.4, 0.2),
    TaxiNode("RWEX", "intersection", 1.1, -0.3),
    TaxiNode("B2", "intersection", 0.7, -0.3),
    TaxiNode("B1", "intersection", 0.3, -0.3),
]

_EDGES = [
    TaxiEdge("G1", "A1", 0.2),
    TaxiEdge("G2", "A1", 0.25),
    TaxiEdge("A1", "A2", 0.4),
    TaxiEdge("A2", "HS_31R", 0.15),
    TaxiEdge("HS_31R", "RW_31R", 0.05, runway="31R"),
    TaxiEdge("RW_31R", "A3", 0.05, runway="31R"),
    TaxiEdge("A3", "HS_31C", 0.3),
    TaxiEdge("RWEX", "B2", 0.25),
    TaxiEdge("B2", "B1", 0.4),
    TaxiEdge("B1", "G1", 0.25),
    TaxiEdge("B1", "G2", 0.2),
    TaxiEdge("RWEX", "A3", 0.2),  # connector enabling a misrouted head-on on taxiway A
]

GRAPH = TaxiGraph(_NODES, _EDGES)


def departure_route(gate: str) -> list[str]:
    """The standard departure route to RWY 31C, crossing RWY 31R (explicit)."""
    return [gate, "A1", "A2", "HS_31R", "RW_31R", "A3", "HS_31C"]


def arrival_route(gate: str) -> list[str]:
    """The standard arrival route via taxiway B — no runway crossing."""
    return ["RWEX", "B2", "B1", gate]


def arrival_route_via_a(gate: str) -> list[str]:
    """A *misrouted* arrival via taxiway A — opposes the departure flow and crosses
    31R the wrong way. Routing an arrival this way is how a bad controller deadlocks."""
    return ["RWEX", "A3", "RW_31R", "HS_31R", "A2", "A1", gate]


def describe() -> str:
    lines = [
        f"FACILITY: {FACILITY} — {FACILITY_NAME} — {POSITION} ({CALLSIGN})",
        f"Departure runway: {DEPARTURE_RUNWAY}. Crossing runway: {CROSSING_RUNWAY} (active).",
        "",
        "TAXIWAYS:",
        "  A (north): Gates G1/G2 - A1 - A2 - [HOLD SHORT 31R] - cross 31R - A3 - HOLD SHORT 31C.",
        "  B (south): RWY exit (RWEX) - B2 - B1 - Gates G1/G2.",
        "  Connector: RWEX - A3 (do not route arrivals via A against the departure flow).",
        "",
        "RULES (§6.2):",
        "  - Departures taxi to 31C via A and must be EXPLICITLY cleared to cross 31R.",
        "  - Never clear a 31R crossing while 31R is hot (arrival/departure on the runway).",
        "  - Route arrivals via B; opposing flows on the same taxiway segment deadlock.",
        "  - Every crossing explicit; every taxi instruction ends with a hold-short.",
    ]
    return "\n".join(lines)
