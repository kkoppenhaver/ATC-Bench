"""Feasibility gate (DESIGN §12.2).

Certification scenarios must be workable — a bust is always the model's fault, never
the generator's. The feasibility check runs the scripted oracle controller against a
candidate scenario and rejects it unless the oracle works it cleanly: no cardinal-class
events in the log and every aircraft served to completion.

The checks inspect the oracle's event log directly rather than calling the scorers:
scorers oracle-normalize E/A by regenerating the scenario (§13.2), and generation now
calls feasibility — going through the scorer here would recurse.
"""

from __future__ import annotations

from ..harness.adapters import ScriptedGNDController
from ..harness.ground_session import GroundSession
from ..scenarios.gnd import GNDScenario


def gnd_feasible(scenario: GNDScenario) -> bool:
    """True iff the oracle works the scenario with no cardinal and full completion."""
    log = GroundSession(scenario).run(ScriptedGNDController()).log
    incursions = [e for e in log.of_type("runway_incursion")
                  if e.payload.get("provenance") == "model"]
    if incursions or log.of_type("deadlock"):
        return False
    spawned = {e.payload["acid"] for e in log.of_type("aircraft_spawn")}
    arrived = {e.payload["acid"] for e in log.of_type("aircraft_arrived")}
    return spawned <= arrived


def twr_feasible(scenario) -> bool:
    """True iff the Tower oracle works the scenario with no cardinal and full completion."""
    from ..harness.adapters import ScriptedTWRController
    from ..harness.tower_session import TowerSession

    log = TowerSession(scenario).run(ScriptedTWRController()).log
    if log.of_type("los_event") or log.of_type("wake_violation"):
        return False
    spawned = {e.payload["acid"] for e in log.of_type("aircraft_spawn")}
    done = ({e.payload["acid"] for e in log.of_type("landed")}
            | {e.payload["acid"] for e in log.of_type("departed_sector")})
    return spawned <= done
