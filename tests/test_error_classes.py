"""Error-class completion (P4.0c; audit m3, DESIGN §8.3/§8.4/§6.3).

CS-CONF, SAY-AGAIN, and BLOCKED at CD; GND-WRONG-TURN; TWR-SLOW-EXIT/TWR-LUAW-MISS.
The oracle-certifies suites prove catchability end-to-end; these tests pin the FSM
mechanics and that the generators actually schedule the classes.
"""

from __future__ import annotations

from atcbench.domain import ErrorEvent, FlightPlan, Persona
from atcbench.pilots.fsm import CDState, PilotFSM
from atcbench.pilots.parser import ParsedTransmission, ParseTier
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scenarios import twr as twr_scenarios


def _plan(acid="AAL100"):
    return FlightPlan(acid=acid, actype="B738", destination="KDTW", filed_sid="MRLW5",
                      filed_altitude=16000, persona=Persona.AIRLINE_CRISP, call_tick=0)


def _clearance(acid="AAL100"):
    return ParsedTransmission(raw="", acid=acid, tier=ParseTier.STANDARD,
                              intent="clearance", altitude=5000, frequency="119.35",
                              squawk="4321", sid="MRLW5", destination="KDTW")


def _correction(acid="AAL100", **kw):
    return ParsedTransmission(raw="", acid=acid, tier=ParseTier.STANDARD,
                              intent="correction", **kw)


def test_say_again_holds_the_clearance_until_repeated():
    fsm = PilotFSM(_plan(), ErrorEvent("SAY-AGAIN", {}))
    fsm.check_in(0)
    verb, _ = fsm.receive_clearance(0, _clearance())
    assert verb["kind"] == "say_again"
    assert fsm.state == CDState.AWAITING_CLEARANCE and not fsm.error_caught
    verb2, _ = fsm.receive_clearance(10, _clearance())
    assert verb2["kind"] == "readback"
    assert fsm.state == CDState.READBACK_PENDING and fsm.error_caught


def test_blocked_readback_is_noise_until_reprompted():
    fsm = PilotFSM(_plan(), ErrorEvent("BLOCKED", {}))
    verb, _ = fsm.receive_clearance(0, _clearance())
    assert verb["kind"] == "blocked_noise"
    assert fsm.comply_as["altitude"] == 5000  # understood correctly, just unreadable
    ack, spurious = fsm.receive_correction(10, _correction())
    assert ack is not None and not spurious and fsm.error_caught


def test_cs_conf_intercept_revert_and_evaporation():
    twin = PilotFSM(_plan("AAL110"), ErrorEvent("CS-CONF", {"confused_with": "AAL100"}))
    twin.state = CDState.AWAITING_CLEARANCE
    assert not twin.error_triggered  # armed, not fired
    verb, ev = twin.intercept_clearance(0, _clearance("AAL100"))
    assert twin.error_triggered and twin.confused
    assert ev["confused_with"] == "AAL100"
    ack, spurious = twin.receive_correction(10, _correction("AAL110"))
    assert ack["kind"] == "standby_ack" and not spurious
    assert twin.state == CDState.AWAITING_CLEARANCE and twin.error_caught

    # Evaporation: the twin gets its OWN clearance first -> the confusion never fires
    # and the scorer must not count it as catchable.
    clean = PilotFSM(_plan("AAL110"), ErrorEvent("CS-CONF", {"confused_with": "AAL100"}))
    clean.state = CDState.AWAITING_CLEARANCE
    clean.receive_clearance(0, _clearance("AAL110"))
    final = clean.finalize(40)
    assert final["error_triggered"] is False


def test_generators_schedule_the_new_classes():
    cd_codes, gnd_codes, twr_codes = set(), set(), set()
    for seed in range(1, 15):
        scn = cd_scenarios.generate(seed, band="heavy", session_seconds=3600)
        cd_codes |= {e.code for e in scn.error_schedule.values()}
        gnd_codes |= {e.code for e in gnd_scenarios.generate(seed, band="heavy").error_schedule.values()}
        twr_codes |= {e.code for e in twr_scenarios.generate(seed, band="heavy").error_schedule.values()}
    assert {"SAY-AGAIN", "BLOCKED", "CS-CONF"} <= cd_codes
    assert "GND-WRONG-TURN" in gnd_codes
    assert {"TWR-SLOW-EXIT", "TWR-LUAW-MISS"} <= twr_codes


def test_oracle_catches_a_triggered_confusion_end_to_end():
    # Heavy seed 1 is a known triggered CS-CONF under the oracle.
    from atcbench.harness.adapters import ScriptedCDController
    from atcbench.harness.session import CDSession

    scn = cd_scenarios.generate(1, band="heavy", session_seconds=3600)
    log = CDSession(scn).run(ScriptedCDController()).log
    confusions = [e for e in log.of_type("aircraft_cleared")
                  if e.payload.get("error_code") == "CS-CONF"
                  and e.payload.get("error_triggered")]
    assert confusions
    assert all(e.payload["error_caught"] for e in confusions)
