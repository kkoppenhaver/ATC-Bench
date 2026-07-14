"""Shared half-duplex channel physics (DESIGN §7.1; audit M4, P4.0a).

Verbosity is an operational cost at every position: transmissions occupy the channel
at 150 wpm, pilots/coordination queue behind traffic, and a controller keying up over
a busy channel at the time-stepped positions is [BLOCKED] and forfeits the sweep.
"""

from __future__ import annotations

from atcbench.harness.adapters import ModelAdapter, ScriptedGNDController
from atcbench.harness.channel import FrequencyChannel, broadcast_seconds
from atcbench.harness.ground_session import GroundSession
from atcbench.scenarios import gnd as gnd_scenarios


def test_channel_queueing_math():
    ch = FrequencyChannel()
    s, e = ch.transmit(10, "alpha bravo charlie delta echo")  # 5 words -> 2 s
    assert (s, e) == (10, 12)
    s2, e2 = ch.transmit(10, "foxtrot golf")  # queues behind the first
    assert s2 == 12 and e2 == 13
    assert ch.is_busy(11) and not ch.is_busy(13)
    assert broadcast_seconds("roger") == 1


class _DoubleKeyer(ModelAdapter):
    """Transmits two instructions in one turn — the second must be stepped on."""

    def __init__(self) -> None:
        self.results: list[list[str]] = []
        self.done = False

    def step(self, obs):
        if obs.get("channel_busy"):
            return self.wait()
        if not self.done and len(obs["aircraft"]) >= 2:
            self.done = True
            from atcbench.harness.adapters import _callsign_words, _runway_spoken
            a, b = obs["aircraft"][0], obs["aircraft"][1]
            def taxi(ac):
                return (f"{_callsign_words(ac['acid'])}, runway {_runway_spoken('31C')}, "
                        f"taxi via alpha, hold short runway {_runway_spoken('31R')}.")
            return {"tool_calls": [
                {"name": "transmit", "input": {"text": taxi(a)}},
                {"name": "transmit", "input": {"text": taxi(b)}},
            ], "text": "", "output_tokens": 1}
        return self.wait()

    def receive_tool_results(self, results):
        self.results.append(results)


def test_double_keying_is_blocked_and_forfeits_the_sweep():
    scn = gnd_scenarios.generate(1)
    adapter = _DoubleKeyer()
    res = GroundSession(scn).run(adapter)
    two = next(r for r in adapter.results if len(r) == 2)
    assert two[0] == "transmitted"
    assert two[1].startswith("[BLOCKED]")
    assert res.log.of_type("blocked_transmission")
    # Only the first instruction produced a clearance.
    burst_ticks = [e.tick for e in res.log.of_type("taxi_clearance")]
    assert len(set(burst_ticks)) == len(burst_ticks)


def test_pilot_readbacks_queue_behind_controller_transmissions():
    scn = gnd_scenarios.generate(1)
    res = GroundSession(scn).run(ScriptedGNDController())
    txs = res.log.of_type("transmission")
    for a, b in zip(txs, txs[1:]):
        # Channel is half-duplex: no transmission starts before the prior one ends,
        # except independent later traffic (start ticks are monotonic per queue).
        if b.payload["start_tick"] < a.payload["end_tick"]:
            raise AssertionError(f"overlap: {a.payload} / {b.payload}")


def test_oracle_still_certifies_under_channel_physics():
    from atcbench.scoring.gnd import score_gnd

    scn = gnd_scenarios.generate(7)
    s = score_gnd(GroundSession(scn).run(ScriptedGNDController()).log, scn.to_dict())
    assert s["gate"] == 1
    assert s["counts"]["blocked_transmissions"] == 0  # oracle never double-keys
