"""Pilot re-calls (P4.0f): ignored pilots re-key instead of waiting forever.

Motivated by the GND calibration probes: a model that checks out mid-session left
ready-to-taxi calls answered by silence, and a single missed transmission converted
deterministically into a NEGLECT cardinal. A re-call after PILOT_RECALL_SEC of
own-radio silence makes one missed call recoverable while sustained inattention
still scores NEGLECT — the recall nudge (90 s) is half the neglect threshold (180 s),
so every neglect is preceded by at least one audible second chance.
"""

from __future__ import annotations

from atcbench.harness.adapters import ModelAdapter, ScriptedGNDController
from atcbench.harness.ground_session import GroundSession
from atcbench.harness.session import CDSession
from atcbench.harness.tower_session import TowerSession
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scenarios import twr as twr_scenarios
from atcbench.scoring.gnd import score_gnd


class _Waiter(ModelAdapter):
    """Checked-out controller: waits every turn, forever."""

    def step(self, observation: dict) -> dict:
        return self.wait()


# --- GND -----------------------------------------------------------------------


def test_gnd_ignored_pilot_recalls_and_neglect_still_fires():
    scn = gnd_scenarios.generate(1, band="standard")
    res = GroundSession(scn).run(_Waiter())
    recalls = res.log.of_type("pilot_recall")
    assert recalls, "ignored pilots must re-call"
    assert {e.payload["reason"] for e in recalls} == {"no_taxi_clearance"}
    # Re-calls repeat: a fully ignored aircraft nags more than once.
    per_acid: dict[str, int] = {}
    for e in recalls:
        per_acid[e.payload["acid"]] = per_acid.get(e.payload["acid"], 0) + 1
    assert max(per_acid.values()) >= 2
    # The re-call is audible on frequency in pilot phraseology.
    texts = [m["text"] for m in res.transcript]
    assert any("still holding at" in t or "awaiting taxi instructions" in t for t in texts)
    # Scoring is unchanged: ignoring the re-calls is still NEGLECT for everyone.
    s = score_gnd(res.log, scn.to_dict())
    assert s["counts"]["neglects"] == s["counts"]["aircraft"]


def test_gnd_prompt_service_means_no_taxi_recalls():
    # The oracle answers every check-in promptly, so no ready-to-taxi nags. Crossing
    # nags are not forbidden outright: the oracle may hold an aircraft at the bar for
    # sequencing reasons the pilot can't hear (and Tower's announced holds already
    # reset the pilot's patience clock) — but they must stay rare.
    scn = gnd_scenarios.generate(42, band="standard")
    res = GroundSession(scn).run(ScriptedGNDController())
    recalls = res.log.of_type("pilot_recall")
    assert not [e for e in recalls if e.payload["reason"] == "no_taxi_clearance"]
    assert len(recalls) <= 2


class _TaxiThenAbandon(ScriptedGNDController):
    """Taxis the first departure properly (route + hold short) then goes silent:
    the pilot ends up parked at the 31R hold bar with no crossing clearance."""

    def __init__(self) -> None:
        super().__init__()
        self._sent = False

    def step(self, observation):
        if observation.get("channel_busy"):
            return self.wait()
        if not self._sent:
            dep = next((a for a in observation["aircraft"] if a["role"] == "departure"), None)
            if dep is not None:
                self._sent = True
                from atcbench.harness.adapters import _callsign_words, _runway_spoken
                return self.transmit(
                    f"{_callsign_words(dep['acid'])}, runway {_runway_spoken('31C')}, "
                    f"taxi via alpha, hold short runway {_runway_spoken('31R')}.")
        return self.wait()


def test_gnd_stranded_at_hold_bar_recalls_for_crossing():
    scn = gnd_scenarios.generate(7, band="standard")
    res = GroundSession(scn).run(_TaxiThenAbandon())
    reasons = {e.payload["reason"] for e in res.log.of_type("pilot_recall")}
    assert "no_crossing_clearance" in reasons
    assert any("holding short runway" in m["text"] for m in res.transcript)


class _HoldEveryone(ScriptedGNDController):
    """Tells each aircraft to hold position once, then goes silent."""

    def __init__(self) -> None:
        super().__init__()
        self._held: set[str] = set()

    def step(self, observation):
        if observation.get("channel_busy"):
            return self.wait()
        for a in observation["aircraft"]:
            if a["acid"] not in self._held:
                self._held.add(a["acid"])
                from atcbench.harness.adapters import _callsign_words
                return self.transmit(f"{_callsign_words(a['acid'])}, hold position.")
        return self.wait()


def test_gnd_held_aircraft_do_not_nag():
    # An explicit "hold position" is service — the pilot complies quietly.
    scn = gnd_scenarios.generate(1, band="standard")
    res = GroundSession(scn).run(_HoldEveryone())
    held_recalls = [e for e in res.log.of_type("pilot_recall")]
    assert not held_recalls


# --- CD ------------------------------------------------------------------------


def test_cd_awaiting_clearance_recalls_before_neglect():
    scn = cd_scenarios.generate(1, band="standard")
    res = CDSession(scn).run(_Waiter())
    recalls = res.log.of_type("pilot_recall")
    assert recalls
    assert {e.payload["reason"] for e in recalls} == {"awaiting_clearance"}
    assert any("still waiting on" in m["text"] for m in res.transcript)
    # Every neglected aircraft re-called at least once before its deadline: the
    # recall (90 s) precedes the neglect threshold (180 s) by construction.
    neglected = {e.payload["acid"] for e in res.log.of_type("aircraft_cleared")
                 if e.payload.get("neglected")}
    recalled = {e.payload["acid"] for e in recalls}
    assert neglected and neglected <= recalled


# --- TWR -----------------------------------------------------------------------


def test_twr_holding_short_departure_recalls_arrivals_do_not():
    scn = twr_scenarios.generate(1, band="standard")
    res = TowerSession(scn).run(_Waiter())
    recalls = res.log.of_type("pilot_recall")
    assert recalls
    assert {e.payload["reason"] for e in recalls} == {"holding_short"}
    roles = {s.acid: s.role for s in scn.all_spawns()}
    assert all(roles[e.payload["acid"]] == "departure" for e in recalls)
    assert any("still holding short" in m["text"] for m in res.transcript)
