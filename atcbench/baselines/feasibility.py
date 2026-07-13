"""Feasibility gate (DESIGN §12.2).

Certification scenarios must be workable — a bust is always the model's fault, never
the generator's. The feasibility check runs the scripted oracle controller against a
candidate scenario and rejects it unless the oracle works it cleanly (gate == 1).
"""

from __future__ import annotations

from ..harness.adapters import ScriptedGNDController
from ..harness.ground_session import GroundSession
from ..scenarios.gnd import GNDScenario
from ..scoring.gnd import score_gnd


def gnd_feasible(scenario: GNDScenario) -> bool:
    """True iff the oracle controller works the scenario without a cardinal violation."""
    result = GroundSession(scenario).run(ScriptedGNDController())
    return score_gnd(result.log, scenario.to_dict())["gate"] == 1
