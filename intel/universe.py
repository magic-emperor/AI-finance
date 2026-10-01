"""
universe.py — which instruments a call may name, and what each is graded against.

Calls on anything that does not resolve here are rejected by the validator,
so the agent cannot invent an ungradeable ticker.
"""
from __future__ import annotations

import re
from typing import Optional

FX_AND_COMMODITIES = {
    "USDINR=X", "EURINR=X", "GBPINR=X", "JPYINR=X",
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "GBPJPY=X",
    "GC=F", "SI=F", "CL=F", "BZ=F",
}
INDICES = {"^NSEI", "^NSEBANK"}
_NS_RE = re.compile(r"^[A-Z0-9&\-]{1,20}\.NS$")

EQUITY_BENCHMARK = "^NSEI"


def resolve_instrument(instrument: str) -> bool:
    if not isinstance(instrument, str):
        return False
    return (instrument in FX_AND_COMMODITIES
            or instrument in INDICES
            or bool(_NS_RE.match(instrument)))


def benchmark_for(instrument: str) -> Optional[str]:
    """Equities are graded relative to Nifty 50. Indices, FX and commodities
    have no benchmark: their signed return is taken as-is."""
    if instrument.endswith(".NS"):
        return EQUITY_BENCHMARK
    return None
