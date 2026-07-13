"""Parser grammar / normalization tests (DESIGN §7.2)."""

from __future__ import annotations

from atcbench.charts import kmdw_cd
from atcbench.pilots import parser as P
from atcbench.pilots.parser import ParseTier


def test_normalize_numbers():
    assert P.normalize("maintain five thousand") == "maintain 5000"
    assert P.normalize("one one niner point three five") == "119.35"
    assert P.normalize("squawk four three two one") == "squawk 4321"
    assert P.normalize("twenty four fifty two") == "2452"


def test_extract_elements():
    assert P.extract_altitude("climb and maintain five thousand") == 5000
    assert P.extract_frequency("departure one one niner point three five") == "119.35"
    assert P.extract_squawk("squawk four three two one") == "4321"


def test_extract_callsign_spoken_and_written():
    cands = ["AAL2452", "UAL881"]
    assert P.extract_callsign("American twenty four fifty two, cleared", cands) == "AAL2452"
    assert P.extract_callsign("AAL2452 readback", cands) == "AAL2452"
    assert P.extract_callsign("United eight eight one", cands) == "UAL881"


def test_clearance_tier_standard():
    text = ("American 2452, cleared to Detroit, Midway Seven departure, maintain five thousand, "
            "departure one one niner point three five, squawk four three two one.")
    pt = P.parse_controller_transmission(text, ["AAL2452"], kmdw_cd.PACK)
    assert pt.intent == "clearance"
    assert pt.tier == ParseTier.STANDARD
    assert pt.altitude == 5000 and pt.squawk == "4321" and pt.frequency == "119.35"


def test_missing_callsign_is_ambiguous():
    pt = P.parse_controller_transmission(
        "cleared to Detroit, maintain five thousand, squawk 4321", ["AAL2452"], kmdw_cd.PACK
    )
    assert pt.tier == ParseTier.AMBIGUOUS
