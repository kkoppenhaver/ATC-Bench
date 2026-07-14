"""Taxi graph model and ground kinematics (DESIGN §4.4).

The airport surface is a graph: nodes (intersections, gates, hold-short bars, runway
crossing points) and edges (taxiway segments; some flagged as runway). Aircraft taxi
along a cleared route (a node list) at type-dependent speed, stopping at *guard* nodes
(hold-short bars protecting a runway) unless the controller has explicitly cleared the
crossing. Nothing implied — every runway crossing is an explicit clearance (§6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Sweeps (5 s each) to traverse 1 NM of taxiway at ~15 kt (~0.0208 NM/sweep).
SWEEPS_PER_NM = 48


@dataclass(frozen=True)
class TaxiNode:
    name: str
    kind: str  # "gate" | "intersection" | "hold_short" | "runway"
    x: float
    y: float
    guard_runway: Optional[str] = None  # entering this node requires a crossing clearance


@dataclass(frozen=True)
class TaxiEdge:
    u: str
    v: str
    length: float  # NM
    runway: Optional[str] = None  # runway name if this segment lies on a runway
    taxiway: Optional[str] = None  # taxiway letter this segment belongs to (lowercase)

    def transit_sweeps(self) -> int:
        return max(1, round(self.length * SWEEPS_PER_NM))


class TaxiGraph:
    def __init__(self, nodes: list[TaxiNode], edges: list[TaxiEdge]):
        self.nodes: dict[str, TaxiNode] = {n.name: n for n in nodes}
        self._edges: dict[tuple[str, str], TaxiEdge] = {}
        for e in edges:
            self._edges[(e.u, e.v)] = e
            self._edges[(e.v, e.u)] = TaxiEdge(e.v, e.u, e.length, e.runway, e.taxiway)

    def edge(self, u: str, v: str) -> Optional[TaxiEdge]:
        return self._edges.get((u, v))

    def neighbors(self, n: str) -> list[str]:
        return [v for (u, v) in self._edges if u == n]

    def node(self, name: str) -> TaxiNode:
        return self.nodes[name]

    def shortest_path(self, src: str, dst: str) -> list[str]:
        """Unweighted BFS shortest path (node list). Used by the oracle/feasibility."""
        from collections import deque

        prev: dict[str, str] = {src: src}
        q = deque([src])
        while q:
            cur = q.popleft()
            if cur == dst:
                break
            for nb in self.neighbors(cur):
                if nb not in prev:
                    prev[nb] = cur
                    q.append(nb)
        if dst not in prev:
            return []
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        return list(reversed(path))

    def route_via(self, src: str, dst: str, taxiways: set[str]) -> list[str]:
        """BFS path from src to dst restricted to edges on the named taxiways —
        the pilot flies the route that was actually transmitted (P4.0b). Returns []
        when the named taxiways don't connect src to dst."""
        from collections import deque

        prev: dict[str, str] = {src: src}
        q = deque([src])
        while q:
            cur = q.popleft()
            if cur == dst:
                break
            for nb in self.neighbors(cur):
                e = self.edge(cur, nb)
                if e is None or e.taxiway not in taxiways or nb in prev:
                    continue
                prev[nb] = cur
                q.append(nb)
        if dst not in prev:
            return []
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        return list(reversed(path))


@dataclass
class GroundAircraft:
    acid: str
    actype: str
    wake: str
    role: str  # "departure" | "arrival"
    spawn_node: str
    goal_node: str
    route: list[str] = field(default_factory=list)  # assigned by the model
    idx: int = 0  # index into route of the node currently occupied
    progress: int = 0  # sweeps into edge route[idx] -> route[idx+1]
    crossings_cleared: set[str] = field(default_factory=set)
    arrived: bool = False
    error_code: Optional[str] = None
    wrong_turned: bool = False  # GND-WRONG-TURN fired for this aircraft
    # provenance: did the model explicitly clear every crossing this aircraft made?
    crossed_without_clearance: bool = False

    def at_node(self) -> str:
        if not self.route:
            return self.spawn_node
        return self.route[min(self.idx, len(self.route) - 1)]

    def next_node(self) -> Optional[str]:
        if self.route and self.idx + 1 < len(self.route):
            return self.route[self.idx + 1]
        return None


def occupied_edge(ac: GroundAircraft) -> Optional[tuple[str, str]]:
    """The directed edge an aircraft is currently traversing, or None if stopped at a node."""
    if ac.progress > 0 and ac.next_node() is not None:
        return (ac.route[ac.idx], ac.route[ac.idx + 1])
    return None
