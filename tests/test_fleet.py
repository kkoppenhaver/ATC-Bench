"""Fleet/callsign realism (P4.0e): GA airframes fly N-numbers, airliners fly flags.

Born from the pilot campaign, where a Cessna 172 flew as "American 4143".
"""

from __future__ import annotations

from atcbench.pilots import parser as P
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scenarios import twr as twr_scenarios
from atcbench.scenarios.fleet import GA_TYPES
from atcbench.verbalizer.template import _callsign_words

SEEDS = (1, 2, 3, 7, 42)


def _check(acid: str, actype: str) -> None:
    if actype in GA_TYPES:
        assert acid.startswith("N") and acid[1:4].isdigit(), (acid, actype)
    else:
        assert not acid.startswith("N"), (acid, actype)


def test_types_and_callsigns_pair_correctly_everywhere():
    for seed in SEEDS:
        for fp in cd_scenarios.generate(seed, band="heavy", session_seconds=3600).flight_plans:
            _check(fp.acid, fp.actype)
        gscn = gnd_scenarios.generate(seed)
        for sp in gscn.departures + gscn.arrivals:
            _check(sp.acid, sp.actype)
        tscn = twr_scenarios.generate(seed)
        for sp in tscn.arrivals + tscn.departures:
            _check(sp.acid, sp.actype)


def test_ga_airframes_get_ga_voices_at_cd():
    from atcbench.domain import Persona

    ga_personas = {Persona.GA_RELAXED, Persona.STUDENT_PILOT}
    seen_ga = False
    for seed in SEEDS:
        for fp in cd_scenarios.generate(seed, band="heavy", session_seconds=3600).flight_plans:
            if fp.actype in GA_TYPES:
                seen_ga = True
                assert fp.persona in ga_personas
            else:
                assert fp.persona not in ga_personas
    assert seen_ga


def test_similar_callsign_twins_are_never_n_numbers():
    for seed in SEEDS:
        scn = cd_scenarios.generate(seed, band="heavy", session_seconds=3600)
        for a, b in scn.similar_pairs:
            assert not a.startswith("N") and not b.startswith("N")


def test_n_number_telephony_and_parsing():
    assert _callsign_words("N714KC") == "November seven one four kilo charlie"
    # Spoken registration resolves against active candidates via its digits.
    acid = P.extract_callsign("November seven one four kilo charlie, ready to taxi",
                              ["N714KC", "AAL2452"])
    assert acid == "N714KC"
    # A single-letter prefix must not soak up unrelated text.
    assert P.extract_callsign("the weather sure is nice today", ["N714KC"]) is None
