"""ATCBench — a closed-loop agentic benchmark for LLM air traffic control.

See DESIGN.md for the full specification. This package currently implements the
Phase 1 "walking skeleton": a deterministic Clearance Delivery (CD) session that
can be run, scored, and replayed byte-identically from a recorded model I/O log.
"""

__version__ = "0.1.0"

# Benchmark constants (pinned; changing any of these is a major version bump —
# see DESIGN.md §4.2, §7.1, §13.2).
HARNESS_VERSION = "0.1.0"
WORDS_PER_SECOND = 2.5  # 150 wpm broadcast rate (§7.1)
TOKENS_PER_SIM_SEC = 25  # R, token-metered regime (§4.2)
